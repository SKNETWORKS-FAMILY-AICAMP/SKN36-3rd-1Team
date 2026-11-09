"""신청기간 확정 기준 회귀 테스트 (실제 DB·API 호출 없음)."""

from copy import deepcopy
from datetime import datetime

import pytest

import backend.eligibility.checker as checker


class FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 11, 10, tzinfo=tz)


@pytest.fixture(autouse=True)
def freeze_korean_today(monkeypatch):
    monkeypatch.setattr(checker, "datetime", FixedDatetime)


def _policy(start=None, end=None, ongoing=None, *, include_ongoing=False):
    policy_node = {}
    if start is not None:
        policy_node["application_start"] = start
    if end is not None:
        policy_node["application_end"] = end
    if include_ongoing:
        policy_node["application_is_ongoing"] = ongoing
    return {
        "policy_id": "POL_TEST_RECEPTION",
        "graph_status": "FOUND",
        "graph": {
            "policy": policy_node,
            "eligibility": {
                "condition_tree": [{
                    "group_id": "ROOT", "logic": "UNKNOWN",
                    "conditions": [], "groups": [],
                }],
                "unknown_logic_groups": [],
                "structure_issues": [],
            },
        },
        "data_quality": {
            "hold_auto_eligibility": False,
            "affected_attributes": [],
        },
    }


@pytest.mark.parametrize(
    "start,end,ongoing,include_ongoing,expected",
    [
        ("2024-02-26", "2025-02-25", None, False, "closed"),  # POL_009
        ("2025-10-19", None, True, True, "open"),  # POL_002
        (None, None, True, True, "open"),  # 시작일 없는 상시 정책
        ("", None, True, True, "open"),  # 빈 문자열도 시작일 미설정으로 취급
        ("2026-12-01", None, True, True, "before"),
        ("2026-12-01", None, None, False, "before"),
        ("2026-12-01", "2027-01-01", None, False, "before"),
        ("2026-11-01", "2026-11-30", None, False, "open"),
        ("2026-11-01", "2026-11-10", None, False, "open"),  # 종료일 당일
        ("2026-11-01", "2026-11-09", None, False, "closed"),
        (None, None, None, False, "unknown"),  # POL_GOV_006·020
        ("2025-10-19", None, None, False, "unknown"),
        (None, "2026-11-30", None, False, "unknown"),
        ("2026-13-01", None, True, True, "unknown"),
        ("2026-11-01", "2026-02-01", None, False, "unknown"),
        ("2026-11-01", "not-a-date", None, False, "unknown"),
        ("2026-11-01", "2026-11-30", True, True, "unknown"),  # 데이터 상충
        (None, None, False, True, "unknown"),
        (None, None, "true", True, "unknown"),
    ],
)
def test_reception_status_cases(start, end, ongoing, include_ongoing, expected):
    policy = _policy(start, end, ongoing, include_ongoing=include_ongoing)
    before = deepcopy(policy)
    result = checker.check_eligibility({"age": 28}, policy)
    assert result["reception_status"] == expected
    assert policy == before


def test_reception_status_uses_graph_policy_not_root():
    policy = _policy()
    policy["application_start"] = "2025-10-19"
    policy["application_is_ongoing"] = True
    policy["verified_reception_period"] = {
        "official_source_checked": True,
        "start_date": "2026-11-01",
        "end_date": "2026-11-30",
    }
    assert checker.check_eligibility({}, policy)["reception_status"] == "unknown"


def test_missing_graph_policy_returns_unknown():
    policy = _policy()
    policy["graph"].pop("policy")
    assert checker.check_eligibility({}, policy)["reception_status"] == "unknown"
