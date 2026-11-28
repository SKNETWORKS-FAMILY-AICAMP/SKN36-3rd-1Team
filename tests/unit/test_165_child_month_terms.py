"""#165: 월령 표현 보정('24개월 이하부터 해당' → '이하에 해당')이 영아·아동 문장에도 적용된다.

generator와 month_age_guard는 같은 단어 목록(CHILD_MONTH_TOPIC)으로 자녀 월령 문장을 알아본다. 가상 문장(sample_only).
"""

import pytest

from backend.llm import generator as g
from backend.llm import month_age_guard


@pytest.mark.parametrize(("text", "expected"), [
    ("영아는 24개월 이하부터 해당합니다.", "영아는 24개월 이하에 해당합니다."),
    ("아동은 **36개월 미만**부터 해당됩니다.", "아동은 **36개월 미만**에 해당됩니다."),
])
def test_infant_and_child_sentences_are_repaired(text, expected):
    assert g._normalize_child_month_bound_wording(text) == expected


@pytest.mark.parametrize("text", [
    "영아는 24개월 이하에 해당합니다.",              # 이미 올바름
    "영아는 6개월부터 24개월까지 해당합니다.",        # 실제 시작 월령
    "아동 월령의 '24개월 이하부터 해당'이라는 표현은 잘못됐습니다.",  # 잘못된 표현 설명
    "근속 기간은 24개월 이하부터 해당합니다.",        # 자녀 문장이 아님
])
def test_other_sentences_are_not_changed(text):
    assert g._normalize_child_month_bound_wording(text) == text


def test_generator_and_guard_share_one_word_list():
    assert g.CHILD_MONTH_TOPIC is month_age_guard.CHILD_MONTH_TOPIC
    for word in ("자녀", "아이", "아기", "영유아", "영아", "아동", "월령"):
        assert month_age_guard.CHILD_MONTH_TOPIC.search(word)
