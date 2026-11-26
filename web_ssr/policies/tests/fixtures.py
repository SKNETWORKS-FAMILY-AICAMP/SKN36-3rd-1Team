"""테스트용 가상 FastAPI 응답(sample_only). 실제 정책 데이터가 아니다."""
from __future__ import annotations

import copy
from datetime import datetime
from unittest import mock

import requests

from policies.services import KST

MANIFEST = "a" * 64


def today() -> str:
    return datetime.now(KST).date().isoformat()


def make_item(policy_id: str = "POL_TEST_001", **overrides) -> dict:
    item = {
        "policy_id": policy_id,
        "policy_name": f"가상 정책 {policy_id}",
        "agency": "가상 기관 담당과",
        "regions": ["전국"],
        "categories": ["가상 분야"],
        "source_url": f"https://example.org/notice/{policy_id}",
        "official_apply_url": None,
        "reception_status": "open",
        "application_schedule": {"evaluated_on": today(), "start_date": None, "end_date": None,
                                 "is_ongoing": True, "days_to_start": None, "days_to_end": None},
        "required_documents": [{"text": "(필수) 가상 신청서", "document_id": f"DOC_{policy_id}",
                                "chunk_id": f"DOC_{policy_id}_C01", "source_url": f"https://example.org/doc/{policy_id}"}],
        "requirement_excerpts": [
            {"label": "지원대상", "text": "가상 지원대상 원문 15세~69세", "document_id": f"DOC_{policy_id}",
             "chunk_id": f"DOC_{policy_id}_C01", "source_url": f"https://example.org/doc/{policy_id}"},
            {"label": "선정기준", "text": "가상 지원대상 원문 15세~69세", "document_id": f"DOC_{policy_id}",
             "chunk_id": f"DOC_{policy_id}_C01", "source_url": f"https://example.org/doc/{policy_id}"},
        ],
    }
    item.update(overrides)
    return item


def make_page(items: list[dict], *, total: int | None = None, offset: int = 0, limit: int = 500, **meta) -> dict:
    page = {"items": items, "total": len(items) if total is None else total, "offset": offset, "limit": limit,
            "dataset_version": "1.4.0-candidate", "manifest_sha256": MANIFEST, "evaluated_on": today(),
            "source_kind": "release_files", "regions": ["전국"], "categories": ["가상 분야"]}
    page.update(meta)
    return page


def response(body=None, *, status: int = 200, invalid_json: bool = False) -> mock.Mock:
    resp = mock.Mock(status_code=status)
    if invalid_json:
        resp.json.side_effect = ValueError("not json")
    else:
        resp.json.return_value = copy.deepcopy(body)
    return resp


def patch_get(*responses):
    """policies.services가 쓰는 requests.get을 순서대로 응답(또는 예외)하게 바꾼다."""
    return mock.patch("policies.services.requests.get", side_effect=list(responses))


TIMEOUT = requests.Timeout("timeout")
CONNECTION_ERROR = requests.ConnectionError("refused")
