"""공공 API 수집기 공통 유틸.

- 인증키는 .env 또는 환경변수에서만 읽는다. 코드·로그·저장 파일에 키를 남기지 않는다.
- HTTP 오류(상태코드)와 API 내부 오류(resultCode 등)를 구분해 예외로 올린다.
- 재시도: 접속 실패 또는 HTTP 429·500·502·503·504에 한해 최대 3회 시도한다.
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
from urllib3.exceptions import ReadTimeoutError

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

    접속 실패(접속 시간 초과 포함) 또는 RETRY_STATUS_CODES만 재시도한다.
    기본 최대 3회 시도, 대기 0.5초 → 1초.
    응답 대기 시간 초과(ReadTimeout)는 서버가 느린 것이라 다시 보내면 대기만 길어지므로 재시도하지 않는다.
    """
    secrets = _param_secrets(params)
    last_error = ""
    for attempt in range(1, max_retries + 1):
        try:
            r = requests.get(url, params=params, timeout=(min(CONNECT_TIMEOUT_SECONDS, timeout), timeout),
                             headers={"User-Agent": user_agent}, allow_redirects=allow_redirects)
        except requests.ConnectionError as e:          # ConnectTimeout도 여기에 포함된다
            # requests는 응답 본문 읽기 시간 초과를 ConnectionError로 감싼다.
            retriable = not any(isinstance(cause, ReadTimeoutError) for cause in e.args)
            last_error = f"{type(e).__name__}: {e}"
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



# 수집 중 오류가 있었는데도 정제 JSONL을 덮어쓰려면 이 환경변수를 1로 둔다(기본은 덮어쓰지 않음).
ALLOW_PARTIAL_ENV = "COLLECTOR_ALLOW_PARTIAL"


def write_processed_jsonl(path: Path, records: list[dict[str, Any]], summary: dict[str, Any],
                          logger: logging.Logger) -> Path:
    """정제 JSONL을 안전하게 쓴다. 실제로 쓴 파일 경로를 돌려준다.

    - 수집 중 오류(summary["errors"])가 있으면 기존 정제 파일을 덮어쓰지 않고 `<이름>.partial.jsonl`에 쓴다.
      중간 실패·쿼터 초과로 일부만 받은 결과가 추적 중인 data/processed 파일을 대체하지 않게 하기 위해서다.
      오류가 있어도 덮어써야 하면 COLLECTOR_ALLOW_PARTIAL=1로 실행한다.
    - 쓰기는 임시 파일에 먼저 한 뒤 교체한다. 쓰는 도중 멈춰도 기존 파일이 반쯤 잘린 채로 남지 않는다.
    """
    errors = summary.get("errors") or []
    allow_partial = os.getenv(ALLOW_PARTIAL_ENV) == "1"
    target = path
    if errors and not allow_partial:
        target = path.with_name(path.stem + ".partial" + path.suffix)
        logger.warning("수집 오류 %d건이 있어 %s를 덮어쓰지 않고 %s에 씁니다. 덮어쓰려면 %s=1로 다시 실행하세요.",
                       len(errors), path.name, target.name, ALLOW_PARTIAL_ENV)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    os.replace(tmp, target)
    summary["refined_jsonl_path"] = str(target)
    summary["refined_jsonl_partial"] = target != path
    return target
