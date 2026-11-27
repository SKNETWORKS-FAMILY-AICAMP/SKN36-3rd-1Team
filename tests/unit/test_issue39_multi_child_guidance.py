"""#39 합의(2026-11-27): child_age_months는 신청 여부를 확인하려는 자녀 한 명의 월령이다.

자녀가 여러 명이면 자녀별로 각각 확인하도록 답변 본문에서 안내한다. 값이 입력돼 있어도 이 안내는
중복 입력 요청으로 지우지 않고, 같은 자녀의 월령을 다시 묻는 문장만 지운다. ask_info 확장은 하지 않는다.
"""

import pytest

from ai.prompts.system_prompt import SYSTEM_PROMPT
from backend.llm import generator as g

BASE = "지원 대상은 24개월 이하 자녀를 양육하는 가정입니다 [1]."


def profile(months):
    return g._normalize_profile({"age": 30, "region": "전북특별자치도", "income": None, "child_age_months": months})


def test_prompt_states_one_child_value_and_per_child_guidance():
    assert "자녀 한 명의 월령" in SYSTEM_PROMPT
    assert "자녀별로 각각 확인하도록 answer 본문에서 안내" in SYSTEM_PROMPT
    # 기존 규칙 유지: 신청자 나이로 자녀 월령을 대신하지 않는다.
    assert "신청자의 age(세)를 자녀의 child_age_months(개월)로 변환하거나 대신 적용하지 마세요" in SYSTEM_PROMPT


@pytest.mark.parametrize("months", [0, 10, 24, 25])
@pytest.mark.parametrize("sentence", [
    "자녀가 여러 명이면 자녀별 월령을 각각 알려 주세요.",
    "자녀가 여러 명이라면 자녀별로 각각 확인해 주세요.",
    "다른 자녀가 있다면 그 자녀의 월령으로 따로 확인해 주세요.",
    "둘째 아이의 월령도 알려 주시면 따로 확인할 수 있어요.",
])
def test_multi_child_guidance_is_kept_when_months_are_known(months, sentence):
    assert not g._is_redundant_request(sentence, profile(months))
    assert sentence in g._remove_redundant_answer_requests(f"{BASE} {sentence}", profile(months))


@pytest.mark.parametrize("months", [0, 24])
@pytest.mark.parametrize("sentence", ["자녀 월령을 알려 주세요.", "아기는 몇 개월인가요?"])
def test_same_child_reask_is_still_removed(months, sentence):
    # 기존 동작 유지: 이미 입력된 같은 자녀의 월령을 다시 묻는 문장은 지운다(0개월 포함).
    assert g._is_redundant_request(sentence, profile(months))
    assert sentence not in g._remove_redundant_answer_requests(f"{BASE} {sentence}", profile(months))


def test_missing_months_keeps_child_age_request():
    # 미입력이면 자녀 월령 안내를 지우지 않는다(기존 동작).
    sentence = "자녀 월령을 알려 주세요."
    assert sentence in g._remove_redundant_answer_requests(f"{BASE} {sentence}", profile(None))


def test_known_region_reask_is_unchanged():
    # 다자녀 예외는 자녀 월령 판정 앞에만 둔다. 이미 입력된 시·도 재요청 판정은 기존대로다.
    assert g._is_redundant_request("거주 지역을 알려 주세요.", profile(10))


def test_ask_info_child_age_months_still_not_allowed():
    from backend.api.schemas import FollowUp
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        FollowUp(kind="ask_info", text="자녀 월령을 알려 주세요.", field="child_age_months")
