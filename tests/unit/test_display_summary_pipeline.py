"""화면용 조건 요약(Checker summarize_conditions_for_display)을 Pipeline 검사에 연결해도 502가 나지 않는지.

가구 원문 검토 안내와 그래프 가구 조건은 한 줄로 합쳐진다(사유를 ' / '로 이음). Pipeline은 원본 행이거나
같은 field·status·label의 원본 사유만 합친 행이면 받아들이고, 원본에 없는 사유·상태는 계속 거부한다.
조건은 가상 값이다(sample_only).
"""

import pytest

from backend.eligibility import checker as c
from backend.rag.pipeline import AskError, _display_check

REVIEW = c._SOURCE_REVIEW_LABELS["household"]


def check(conditions):
    return {"policy_id": "POL_SAMPLE", "conditions": conditions}


ORIGINAL = [
    {"field": "age", "label": "나이", "status": "eligible", "reason": "만 19~34세 충족 (sample_only)"},
    {"field": "housing", "label": "가구", "status": "needs_check", "reason": "무주택세대 (sample_only)"},
    {"field": "housing", "label": REVIEW, "status": "needs_check", "reason": "가구원 수 확인 (sample_only)"},
]


def test_real_summary_function_passes_pipeline_check():
    shown = _display_check(check([dict(x) for x in ORIGINAL]), c.summarize_conditions_for_display)["conditions"]
    housing = [x for x in shown if x["field"] == "housing"]
    assert len(housing) == 1
    assert housing[0]["reason"] == "무주택세대 (sample_only)" + c.DISPLAY_REASON_JOINER + "가구원 수 확인 (sample_only)"


def test_invented_reason_is_still_rejected():
    def invent(rows):
        return [rows[0], {**rows[1], "reason": "무주택세대 (sample_only) / 지어낸 조건"}]

    with pytest.raises(AskError):
        _display_check(check([dict(x) for x in ORIGINAL]), invent)


def test_single_reason_with_other_label_is_rejected():
    def relabel(rows):
        return [rows[0], {**rows[1], "label": "지어낸 라벨"}]

    with pytest.raises(AskError):
        _display_check(check([dict(x) for x in ORIGINAL]), relabel)


def test_status_change_is_still_rejected():
    def promote(rows):
        return [rows[0], {**rows[1], "status": "eligible"}, rows[2]]

    with pytest.raises(AskError):
        _display_check(check([dict(x) for x in ORIGINAL]), promote)
