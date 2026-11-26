"""FastAPI 정책 데이터 조회.

현재 FastAPI에는 정책 단건 API가 없어 기존 읽기 전용 목록 API(GET /api/policies, docs/api/ask_contract.md v1.5)를
끝까지 받아 policy_id가 **완전히 일치하는 한 건**만 고른다. 정책명 부분 검색(q)은 쓰지 않는다.
나중에 단건 API가 생기면 이 모듈의 fetch_policy만 바꾸면 되도록 View·Template은 반환 형식에만 의존한다.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from concurrent.futures import Future
from datetime import datetime, timedelta, timezone
from typing import Any
from threading import Lock
from urllib.parse import urlsplit

import requests
from django.conf import settings
from django.core.cache import cache

from .converters import is_policy_id

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
PAGE_SIZE = 500  # FastAPI limit 최대값(main.py Query(le=500))
MAX_PAGES = 50  # 응답 이상으로 끝나지 않는 반복을 막는다
RECEPTION_STATUSES = frozenset({"before", "open", "closed", "unknown"})
ISO_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_catalog_lock = Lock()  # 같은 프로세스의 동시 캐시 미스만 합친다.
_catalog_pending: dict[str, Future] = {}  # 진행 중 결과만 공유하며 완료 즉시 제거한다.
SHA256 = re.compile(r"^[a-f0-9]{64}$")


class PolicyServiceError(Exception):
    """정책 조회 실패의 공통 부모."""


class PolicyNotFound(PolicyServiceError):
    """정상 목록에 해당 policy_id가 없다(404)."""


class PolicyServiceUnavailable(PolicyServiceError):
    """FastAPI 연결 실패·타임아웃·비정상 응답(503). 사용자에게 내부 사유를 보여 주지 않는다."""


@dataclass(frozen=True)
class PolicyResult:
    policy: dict[str, Any]
    dataset_version: str
    manifest_sha256: str
    evaluated_on: str


# ---------------------------------------------------------------------------
# 값 검증 도우미
# ---------------------------------------------------------------------------

def safe_http_url(value: object) -> str | None:
    """http/https이고 호스트가 있으며 공백·계정 정보가 없는 URL만 돌려준다(backend valid_source_url과 같은 기준)."""
    if not isinstance(value, str) or not value or any(char.isspace() for char in value):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None:
            return None
        if parsed.port is not None and parsed.port <= 0:
            return None
    except ValueError:
        return None
    return value


def is_iso_day(value: object) -> bool:
    if not isinstance(value, str) or not ISO_DAY.match(value):
        return False
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%Y-%m-%d") == value
    except ValueError:
        return False


def _nonblank(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if _nonblank(item)]


def _int_or_none(value: object) -> bool:
    return value is None or (isinstance(value, int) and not isinstance(value, bool))


def normalize_schedule(value: object) -> dict[str, Any] | None:
    """ApplicationSchedule 계약(schemas.py)에 맞는 값만 받는다. 틀리면 일정을 표시하지 않는다(None)."""
    if not isinstance(value, dict):
        return None
    start, end = value.get("start_date"), value.get("end_date")
    if not (is_iso_day(value.get("evaluated_on"))
            and (start is None or is_iso_day(start)) and (end is None or is_iso_day(end))
            and isinstance(value.get("is_ongoing"), bool)
            and _int_or_none(value.get("days_to_start")) and _int_or_none(value.get("days_to_end"))):
        return None
    if value["is_ongoing"] and end:
        return None
    if start and end and start > end:
        return None
    return {key: value.get(key) for key in
            ("evaluated_on", "start_date", "end_date", "is_ongoing", "days_to_start", "days_to_end")}


def _normalize_excerpts(value: object, *, with_label: bool) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    rows = []
    for item in value:
        if not isinstance(item, dict) or not _nonblank(item.get("text")):
            continue
        if not _nonblank(item.get("document_id")) or not _nonblank(item.get("chunk_id")):
            continue
        if with_label and not _nonblank(item.get("label")):
            continue
        row = {"text": item["text"].strip(), "document_id": item["document_id"], "chunk_id": item["chunk_id"],
               "source_url": safe_http_url(item.get("source_url"))}
        if with_label:
            row["label"] = item["label"].strip()
        rows.append(row)
    return rows


def merge_same_excerpts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """같은 문서·청크·본문인 [지원대상]·[선정기준] 발췌는 라벨만 합쳐 한 번 보여 준다(원문은 바꾸지 않는다)."""
    merged: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        key = (row["document_id"], row["chunk_id"], row["text"])
        if key in merged:
            if row["label"] not in merged[key]["labels"]:
                merged[key]["labels"].append(row["label"])
            continue
        merged[key] = {**row, "labels": [row["label"]]}
    return list(merged.values())


def normalize_policy(item: dict[str, Any]) -> dict[str, Any]:
    """화면에 쓰는 필드만 꺼낸다. 필수 필드(policy_id·policy_name·reception_status)가 틀리면 503으로 처리한다."""
    if not is_policy_id(item.get("policy_id")) or not _nonblank(item.get("policy_name")):
        raise PolicyServiceUnavailable("정책 필수 필드 누락")
    if not isinstance(item.get("reception_status"), str) or item["reception_status"] not in RECEPTION_STATUSES:
        raise PolicyServiceUnavailable("접수 상태 값 오류")
    source_url = safe_http_url(item.get("source_url"))
    apply_url = safe_http_url(item.get("official_apply_url"))
    return {
        "policy_id": item["policy_id"],
        "policy_name": item["policy_name"].strip(),
        "agency": item["agency"].strip() if _nonblank(item.get("agency")) else "",
        "regions": _string_list(item.get("regions")),
        "categories": _string_list(item.get("categories")),
        "source_url": source_url,
        # 신청 주소가 원문 주소와 같으면 별도 신청 링크로 보지 않는다(React PolicyCard와 같은 기준).
        "official_apply_url": apply_url if apply_url and apply_url != source_url else None,
        "reception_status": item["reception_status"],
        "application_schedule": normalize_schedule(item.get("application_schedule")),
        "required_documents": _normalize_excerpts(item.get("required_documents"), with_label=False),
        "requirement_excerpts": merge_same_excerpts(
            _normalize_excerpts(item.get("requirement_excerpts"), with_label=True)),
    }


# ---------------------------------------------------------------------------
# FastAPI 호출
# ---------------------------------------------------------------------------

def _get_page(offset: int) -> dict[str, Any]:
    url = f"{settings.SSR_FASTAPI_BASE_URL}/api/policies"
    timeout = (settings.SSR_FASTAPI_CONNECT_TIMEOUT_SECONDS, settings.SSR_FASTAPI_READ_TIMEOUT_SECONDS)
    try:
        response = requests.get(url, params={"limit": PAGE_SIZE, "offset": offset}, timeout=timeout,
                                headers={"Accept": "application/json"}, allow_redirects=False)
    except requests.Timeout as exc:
        raise PolicyServiceUnavailable("정책 API 시간 초과") from exc
    except requests.RequestException as exc:
        raise PolicyServiceUnavailable("정책 API 연결 실패") from exc
    if response.status_code != 200:
        raise PolicyServiceUnavailable(f"정책 API 상태 코드 {response.status_code}")
    try:
        body = response.json()
    except ValueError as exc:
        raise PolicyServiceUnavailable("정책 API JSON 해석 실패") from exc
    if not isinstance(body, dict):
        raise PolicyServiceUnavailable("정책 API 응답 형식 오류")
    return body


def _check_page(page: dict[str, Any], expected_offset: int) -> None:
    items, total, offset, limit = page.get("items"), page.get("total"), page.get("offset"), page.get("limit")
    ints = all(isinstance(value, int) and not isinstance(value, bool) for value in (total, offset, limit))
    if not (isinstance(items, list) and ints and total >= 0 and offset == expected_offset and limit > 0
            and len(items) <= limit and offset + len(items) <= total):
        raise PolicyServiceUnavailable("정책 목록 페이지 형식 오류")
    if not (_nonblank(page.get("dataset_version")) and isinstance(page.get("manifest_sha256"), str)
            and SHA256.fullmatch(page["manifest_sha256"]) and is_iso_day(page.get("evaluated_on"))
            and page.get("source_kind") == "release_files"):
        raise PolicyServiceUnavailable("정책 목록 메타데이터 오류")
    if any(not isinstance(item, dict) or not is_policy_id(item.get("policy_id")) for item in items):
        raise PolicyServiceUnavailable("정책 목록 항목 형식 오류")


def load_catalog() -> dict[str, Any]:
    """목록을 끝까지 받는다. 페이지 사이에 데이터 버전·manifest·기준일·총 건수가 바뀌면 실패로 본다."""
    first: dict[str, Any] | None = None
    items: list[dict[str, Any]] = []
    for _ in range(MAX_PAGES):
        page = _get_page(len(items))
        _check_page(page, len(items))
        if first is None:
            first = page
        elif any(page[key] != first[key] for key in ("dataset_version", "manifest_sha256", "evaluated_on", "total")):
            raise PolicyServiceUnavailable("조회 중 정책 데이터가 바뀜")
        items.extend(page["items"])
        if len(items) >= first["total"]:
            break
        if not page["items"]:
            raise PolicyServiceUnavailable("정책 목록이 중간에 비어 있음")
    else:
        raise PolicyServiceUnavailable("정책 목록 페이지 수 초과")
    assert first is not None
    ids = [item["policy_id"] for item in items]
    if len(ids) != first["total"] or len(set(ids)) != len(ids):
        raise PolicyServiceUnavailable("정책 목록 건수 또는 ID 중복 오류")
    return {"by_id": dict(zip(ids, items)), "dataset_version": first["dataset_version"],
            "manifest_sha256": first["manifest_sha256"], "evaluated_on": first["evaluated_on"]}


def _cached_catalog() -> dict[str, Any]:
    seconds = settings.SSR_CATALOG_CACHE_SECONDS
    if seconds <= 0:
        return load_catalog()
    # 접수 상태는 날짜에 따라 바뀌므로 한국 날짜가 바뀌면 캐시를 다시 만든다. 실패 결과는 캐시하지 않는다.
    key = f"ssok-ssr:policy-catalog:{datetime.now(KST).date().isoformat()}"
    catalog = cache.get(key)
    if catalog is not None:
        return catalog
    with _catalog_lock:
        catalog = cache.get(key)
        if catalog is not None:
            return catalog
        pending = _catalog_pending.get(key)
        leader = pending is None
        if leader:
            pending = _catalog_pending[key] = Future()
    if not leader:
        # 같은 갱신을 기다리는 요청에는 실패도 공유해 타임아웃이 순차 누적되지 않게 한다.
        return pending.result()
    try:
        catalog = load_catalog()
        cache.set(key, catalog, seconds)
        pending.set_result(catalog)
        return catalog
    except BaseException as exc:
        pending.set_exception(exc)
        raise
    finally:
        with _catalog_lock:
            _catalog_pending.pop(key, None)


def fetch_policy(policy_id: str) -> PolicyResult:
    if not is_policy_id(policy_id):
        raise PolicyNotFound("정책 ID 형식 오류")
    catalog = _cached_catalog()
    item = catalog["by_id"].get(policy_id)
    if item is None:
        raise PolicyNotFound("정책 없음")
    policy = normalize_policy(item)
    if policy["policy_id"] != policy_id:  # 정규화 이후에도 요청 ID와 같은지 한 번 더 확인
        raise PolicyServiceUnavailable("정책 ID 불일치")
    return PolicyResult(policy=policy, dataset_version=catalog["dataset_version"],
                        manifest_sha256=catalog["manifest_sha256"], evaluated_on=catalog["evaluated_on"])
