"""공공 API 수집기 공통 유틸.

- 인증키는 .env 또는 환경변수에서만 읽는다. 코드·로그·저장 파일에 키를 남기지 않는다.
- HTTP 오류(상태코드)와 API 내부 오류(resultCode 등)를 구분해 예외로 올린다.
- 재시도: 접속 실패·5xx·429에 한해 짧은 백오프(최대 3회 시도, 대기 0.5초 → 1초).
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, quote_plus

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
        record.msg, record.args = mask_secrets(record.getMessage(), *self.secrets), ()
        return True


def get_logger(name: str, secrets: list[str]) -> logging.Logger:
    lg = logging.getLogger(name)
    lg.addFilter(MaskingFilter(secrets))
    return lg


RETRY_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


def mask_secrets(text: Any, *secrets: str | None) -> str:
    """문자열에서 인증키 값을 *** 로 가린다. requests 예외 문장에는 쿼리(키 포함) URL이 들어 있다."""
    masked = str(text)
    for secret in secrets:
        if not secret or len(secret) < 4:
            continue
        # requests는 쿼리 값을 URL 인코딩하므로(예: + → %2B) 원래 값과 인코딩된 값을 모두 가린다.
        for form in {secret, quote_plus(secret), quote(secret, safe="")}:
            masked = masked.replace(form, "***")
    return masked


def _param_secrets(params: dict[str, Any]) -> list[str]:
    """요청 파라미터 중 이름에 key가 들어간 값(serviceKey, apiKeyNm 등)."""
    return [str(value) for name, value in params.items() if "key" in name.lower() and value]


CONNECT_TIMEOUT_SECONDS = 5      # 접속 자체가 안 되는 경우는 빨리 실패시킨다 (응답 대기 시간은 timeout 인자)


def http_get(url: str, params: dict[str, Any], *, logger: logging.Logger, timeout: int = 30,
             max_retries: int = 3, backoff: float = 0.5, user_agent: str = USER_AGENT,
             allow_redirects: bool = False) -> requests.Response:
    """GET with retry/backoff. 오류 문장에서 인증키를 가린다.

    재시도: 접속 실패(접속 시간 초과 포함)·429·5xx만, 최대 max_retries번, 대기 0.5초 → 1초 (기본 합계 1.5초).
    응답 대기 시간 초과(ReadTimeout)는 서버가 느린 것이라 다시 보내면 대기만 길어지므로 재시도하지 않는다.
    """
    secrets = _param_secrets(params)
    last_error = ""
    for attempt in range(1, max_retries + 1):
        try:
            r = requests.get(url, params=params, timeout=(min(CONNECT_TIMEOUT_SECONDS, timeout), timeout),
                             headers={"User-Agent": user_agent}, allow_redirects=allow_redirects)
        except requests.ConnectionError as e:          # ConnectTimeout도 여기에 포함된다
            retriable, last_error = True, f"{type(e).__name__}: {e}"
        except requests.Timeout as e:
            retriable, last_error = False, f"{type(e).__name__}: {e}"
        else:
            if r.status_code == 200:
                return r
            # 3xx·4xx(429 제외)는 다시 보내도 결과가 같으므로 재시도하지 않는다.
            retriable = r.status_code in RETRY_STATUS_CODES
            last_error = f"HTTP {r.status_code}" + ("" if retriable else f": {r.text[:300]}")
        last_error = mask_secrets(last_error, *secrets)
        logger.warning("request failed (attempt %d/%d): %s", attempt, max_retries, last_error[:200])
        if not retriable or attempt == max_retries:
            break
        time.sleep(backoff * 2 ** (attempt - 1))
    raise RuntimeError(f"request to {url.split('?')[0]} failed: {last_error[:300]}")


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

