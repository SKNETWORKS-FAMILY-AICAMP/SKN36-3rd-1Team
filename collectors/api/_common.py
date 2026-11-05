"""공공 API 수집기 공통 유틸.

- 인증키는 .env 또는 환경변수에서만 읽는다. 코드·로그·저장 파일에 키를 남기지 않는다.
- HTTP 오류(상태코드)와 API 내부 오류(resultCode 등)를 구분해 예외로 올린다.
- 재시도: 네트워크 오류·5xx·429에 한해 지수 백오프(최대 4회).
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

BASE_DIR = Path(__file__).resolve().parents[2]
RAW_ROOT = BASE_DIR / "data" / "raw"
KST = timezone(timedelta(hours=9))
USER_AGENT = "LifePolicyAI-academic-project/0.3 (SKN36 bootcamp)"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


class ApiError(RuntimeError):
    """API가 HTTP 200을 반환했지만 내부 결과 코드가 실패인 경우."""


def now_kst() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


def load_env_value(*names: str) -> str | None:
    for n in names:
        v = os.getenv(n)
        if v:
            return v.strip()
    env_path = BASE_DIR / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() in names:
                v = v.strip().strip('"').strip("'")
                if v:
                    return v
    return None


class MaskingFilter(logging.Filter):
    def __init__(self, secrets: list[str]):
        super().__init__()
        self.secrets = [s for s in secrets if s]

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        for s in self.secrets:
            msg = msg.replace(s, "***")
        record.msg, record.args = msg, ()
        return True


def get_logger(name: str, secrets: list[str]) -> logging.Logger:
    lg = logging.getLogger(name)
    lg.addFilter(MaskingFilter(secrets))
    return lg


def http_get(url: str, params: dict[str, Any], *, logger: logging.Logger, timeout: int = 30,
             max_retries: int = 4, backoff: float = 1.5) -> requests.Response:
    """GET with retry/backoff. 예외 메시지에 URL(키 포함)을 넣지 않는다."""
    last_exc: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            r = requests.get(url, params=params, timeout=timeout,
                             headers={"User-Agent": USER_AGENT}, allow_redirects=False)
            if r.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"HTTP {r.status_code}")
            if r.status_code != 200:
                # 4xx/3xx는 재시도 의미 없음
                raise requests.HTTPError(f"HTTP {r.status_code}: {r.text[:300]}")
            return r
        except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as e:
            last_exc = e
            retriable = not (isinstance(e, requests.HTTPError) and str(e).startswith("HTTP 4"))
            retriable = retriable and not (isinstance(e, requests.HTTPError) and str(e).startswith("HTTP 3"))
            logger.warning("request failed (attempt %d/%d): %s", attempt, max_retries, str(e)[:200])
            if not retriable or attempt == max_retries:
                break
            time.sleep(backoff ** attempt)
    raise RuntimeError(f"request to {url.split('?')[0]} failed: {str(last_exc)[:300]}")


def save_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def write_run_log(source: str, summary: dict[str, Any]) -> Path:
    """수집 실행 요약(키 미포함)을 data/raw/<source>/run_log.jsonl에 추가."""
    p = RAW_ROOT / source / "run_log.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(summary, ensure_ascii=False) + "\n")
    return p

