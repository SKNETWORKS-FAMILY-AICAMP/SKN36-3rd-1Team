"""Only official evidence can establish the start of a child-month range."""
from copy import deepcopy
import json

import pytest

from backend.llm import generator as g
from backend.llm.month_age_guard import ground_child_month_ranges


def evidence(text="자녀 월령은 24개월 이하입니다.", source_id=1, policy_id="SAMPLE_A"):
    return {"source_id": source_id, "policy_id": policy_id, "text": text}


def response(answer):
    return {"status": "answered", "answer": answer, "used_source_ids": [1],
            "follow_up_questions": [{"kind": "question", "text": "신청 서류는 무엇인가요?"}]}


@pytest.mark.parametrize("range_text", [
    "0개월부터 24개월까지", "3개월부터 24개월까지", "0~24개월",
    "0개월~24개월", "0개월 이상 24개월 이하", "**0개월부터 24개월까지**",
    "**0개월**부터 **24개월**까지", "0개월부터 **24개월까지**",
])
def test_unstated_lower_endpoint_is_not_presented_as_official(range_text):
    raw = response(f"자녀 월령의 신청 가능한 범위는 {range_text}로 확인됩니다 [1].")
    before = deepcopy(raw)
    out = ground_child_month_ranges(raw, [evidence()])
    assert "범위는 24개월 이하로 확인됩니다" in out["answer"]
    assert "시작 월령은 이번 근거에서 확인되지 않았습니다 [1]." in out["answer"]
    assert raw == before
    assert out["follow_up_questions"] == raw["follow_up_questions"]
    assert out["used_source_ids"] == [1]


@pytest.mark.parametrize("official", [
    "자녀 월령은 0개월부터 24개월까지입니다.",
    "자녀 월령은 0~24개월입니다.",
    "자녀 월령은 0개월 이상, 24개월 이하입니다.",
    "자녀는 출생부터 신청할 수 있고, 자녀 월령은 24개월 이하입니다.",
    "신생아가 대상이며 자녀 월령은 24개월 이하입니다.",
])
def test_explicit_lower_or_birth_evidence_is_not_replaced_with_unknown(official):
    raw = response("자녀 월령 기준은 0개월부터 24개월까지입니다 [1].")
    assert ground_child_month_ranges(raw, [evidence(official)]) == raw


def test_lower_bound_in_another_chunk_of_same_policy_is_preserved():
    raw = response("자녀 월령 기준은 3개월부터 24개월까지입니다 [1].")
    assert ground_child_month_ranges(raw, [evidence(), evidence(
        "자녀 월령은 생후 3개월부터입니다.", 2)]) == raw


def test_another_policy_cannot_supply_this_policys_lower_bound():
    raw = response("자녀 월령의 신청 가능한 범위는 0개월부터 24개월까지입니다 [1].")
    out = ground_child_month_ranges(raw, [evidence(), evidence(
        "자녀 월령은 0개월부터 24개월까지입니다.", 2, "SAMPLE_B")])
    assert "시작 월령은 이번 근거에서 확인되지 않았습니다" in out["answer"]
    assert "0개월부터" not in out["answer"]


@pytest.mark.parametrize("answer", [
    "자녀 월령 기준은 0개월부터 24개월까지입니다 [1][2].",
    "자녀 월령 기준은 0개월부터 24개월까지입니다.",
])
def test_ambiguous_multi_policy_paragraph_is_not_rewritten(answer):
    raw = response(answer)
    assert ground_child_month_ranges(raw, [evidence(), evidence(
        "자녀 월령은 36개월 이하입니다.", 2, "SAMPLE_B")]) == raw


@pytest.mark.parametrize("answer", [
    "자녀 월령 기준은 24개월 이하이며 시작 월령은 확인되지 않았습니다 [1].",
    "자녀 월령 기준은 0개월부터 36개월까지입니다 [1].",
    "자녀 월령은 24개월 이하입니다. 지원 기간은 0개월부터 24개월까지입니다 [1].",
    "자녀 월령은 24개월 이하입니다. 지원금은 0~24개월 동안 지급됩니다 [1].",
    "자녀 월령은 24개월 이하입니다. 지원금은 0~24개월간 지급됩니다 [1].",
    "자녀 월령 기준은 24개월 이하입니다. 서비스는 0~24개월에 걸쳐 제공됩니다 [1].",
    "자녀 월령 기준은 24개월 이하입니다. 서비스는 **0~24개월**에 걸쳐 제공됩니다 [1].",
    "자녀 월령의 '0개월부터 24개월까지'라는 표현은 잘못됐습니다 [1].",
])
def test_other_claims_and_already_correct_answers_are_unchanged(answer):
    raw = response(answer)
    assert ground_child_month_ranges(raw, [evidence()]) == raw


def test_conflicting_upper_bounds_do_not_produce_a_guessed_correction():
    raw = response("자녀 월령 기준은 0개월부터 24개월까지입니다 [1].")
    assert ground_child_month_ranges(raw, [evidence(), evidence(
        "장애 아동의 자녀 월령은 36개월 이하입니다.", 2)]) == raw


@pytest.mark.parametrize("range_text,expected", [
    ("0~24개월", "24개월 미만"),
    ("0개월 이상 24개월 미만", "24개월 미만"),
    ("0개월 이상 24개월 **미만**", "24개월 미만"),
    ("0~24개월 (원문 기준)", "24개월 미만 (원문 기준)"),
])
def test_exclusive_upper_bound_keeps_its_comparator(range_text, expected):
    out = ground_child_month_ranges(response(f"자녀 월령의 신청 범위는 {range_text}입니다 [1]."),
                                    [evidence("자녀 월령은 24개월 미만입니다.")])
    assert f"신청 범위는 {expected}입니다" in out["answer"]
    assert "24개월 이하" not in out["answer"]


@pytest.mark.parametrize("official_comparison,answer_comparison", [
    ("이하", "미만"),
    ("미만", "이하"),
    ("이하", "**미만**"),
])
def test_conflicting_answer_comparator_is_preserved(official_comparison, answer_comparison):
    raw = response(f"자녀 월령 신청 범위는 0개월 이상 24개월 {answer_comparison}입니다 [1].")
    assert ground_child_month_ranges(raw, [evidence(
        f"자녀 월령은 24개월 {official_comparison}입니다.",
    )]) == raw


@pytest.mark.parametrize("official", [
    "자녀 월령은 24개월 이하로 제한하지 않습니다.",
    "자녀 월령이 24개월 이하인 경우는 지원 대상에서 제외합니다.",
    "자녀 월령이 24개월 이하인 경우 지원 대상에서 제외됩니다.",
    "지원 대상 제외 기준은 자녀 월령 24개월 이하입니다.",
    "신청 불가 기준은 자녀 월령 24개월 이하입니다.",
])
def test_nonasserted_upper_bounds_cannot_supply_a_correction(official):
    raw = response("자녀 지원 기준은 0개월부터 신청 가능합니다 [1].")
    assert ground_child_month_ranges(raw, [evidence(official)]) == raw


@pytest.mark.parametrize("official", [
    "중복 신청은 불가합니다. 자녀 월령은 24개월 이하입니다.",
    "자녀 월령은 24개월 이하입니다. 타 사업 참여 가구는 지원에서 제외됩니다.",
])
def test_unrelated_negative_sentences_do_not_block_age_correction(official):
    raw = response("자녀 월령은 0개월부터 신청 가능합니다 [1].")
    out = ground_child_month_ranges(raw, [evidence(official)])
    assert "24개월 이하인 경우 신청 가능합니다" in out["answer"]


def test_each_policys_paragraph_is_grounded_separately():
    raw = response("A의 자녀 월령 신청 범위는 0~24개월입니다 [1].\n\n"
                   "B의 자녀 월령 신청 범위는 3개월부터 24개월까지입니다 [2].")
    out = ground_child_month_ranges(raw, [evidence(), evidence(
        "자녀 월령은 3개월부터 24개월까지입니다.", 2, "SAMPLE_B")])
    assert "A의 자녀 월령 신청 범위는 24개월 이하입니다" in out["answer"]
    assert "B의 자녀 월령 신청 범위는 3개월부터 24개월까지입니다 [2]." in out["answer"]


@pytest.mark.parametrize("months", [None, 0, 24])
def test_actual_model_range_is_grounded_in_generate_answer(monkeypatch, months):
    answer = ("이 정책의 자녀 월령 기준은 **24개월 이하**입니다. "
              "따라서 원문상 신청 가능한 범위는 0개월부터 24개월까지로 확인됩니다. "
              "다만 이 정책은 실제 복지정책이 아니라 회귀검증용 가상 정책이며, "
              "접수 일정은 확인되지 않았습니다. [1]")
    monkeypatch.setattr(g, "_call_openai", lambda messages: json.dumps(response(answer), ensure_ascii=False))
    out = g.generate_answer("아이는 몇 개월부터 몇 개월까지 신청할 수 있나요?",
        [evidence()], [], profile={"age": 30, "region": "서울특별시", "income": 300,
                                  "child_age_months": months})
    assert "0개월부터" not in out["answer"]
    assert "24개월 이하로 확인됩니다" in out["answer"]
    assert "접수 일정은 확인되지 않았습니다" in out["answer"]
    assert "시작 월령은 이번 근거에서 확인되지 않았습니다 [1]." in out["answer"]


@pytest.mark.parametrize("claim", [
    "0개월부터 신청할 수 있습니다", "생후 0개월부터 신청 가능합니다",
    "3개월부터 이용할 수 있습니다", "0개월부터 지원을 받을 수 있습니다",
    "0개월부터 해당합니다", "시작 월령은 0개월입니다", "최소 월령은 **0개월**입니다",
])
def test_unknown_start_is_not_invented_when_answer_does_not_repeat_upper(claim):
    out = ground_child_month_ranges(response(f"자녀 지원 기준 안내: {claim} [1]."), [evidence()])
    assert "0개월부터" not in out["answer"]
    assert "3개월부터" not in out["answer"]
    assert "0개월입니다" not in out["answer"]
    assert "시작 월령은 이번 근거에서 확인되지 않았습니다" in out["answer"]


@pytest.mark.parametrize("comparison,claim", [
    ("이하", "25개월부터 신청 가능합니다"),
    ("미만", "생후 **24개월**부터 신청 가능합니다"),
    ("이하", "시작 월령은 25개월입니다"),
    ("이하", "신청 가능한 범위는 25개월부터 24개월까지입니다"),
    ("미만", "신청 가능한 범위는 24개월 이상 24개월 미만입니다"),
])
def test_start_outside_evidence_upper_bound_is_preserved(comparison, claim):
    raw = response(f"자녀 지원 기준 안내: {claim} [1].")
    assert ground_child_month_ranges(raw, [evidence(f"자녀 월령은 24개월 {comparison}입니다.")]) == raw


def test_start_equal_to_inclusive_upper_bound_can_be_corrected():
    raw = response("자녀 지원 기준은 24개월부터 신청 가능합니다 [1].")
    out = ground_child_month_ranges(raw, [evidence()])
    assert "24개월 이하인 경우 신청 가능합니다" in out["answer"]
    assert "시작 월령은 이번 근거에서 확인되지 않았습니다" in out["answer"]


@pytest.mark.parametrize("claim", [
    "자녀는 0개월부터 지원을 받고 있습니다 [1].",
    "자녀는 0개월부터 신청할 수 있는지 확인이 필요합니다 [1].",
    "자녀는 0개월부터 신청할 수 없습니다 [1].",
])
def test_personal_history_and_nonassertive_start_mentions_are_preserved(claim):
    raw = response(claim)
    assert ground_child_month_ranges(raw, [evidence()]) == raw


@pytest.mark.parametrize("other", [
    "생후 3개월이 되면 신청할 수 있습니다.",
    "생후 세 개월이 되면 신청할 수 있습니다.",
    "휴직 기간은 12개월입니다.",
])
def test_unparsed_month_expressions_are_not_treated_as_missing_lower_bound(other):
    raw = response("자녀 월령 신청 범위는 0~24개월입니다 [1].")
    assert ground_child_month_ranges(raw, [evidence("자녀 월령은 24개월 이하입니다. " + other)]) == raw


def test_quality_warning_prevents_automatic_fact_rewrite():
    raw = response("자녀 월령 신청 범위는 0~24개월입니다 [1].")
    context = {**evidence(), "quality_warnings": ["sample_only_ambiguous_age_evidence"]}
    assert ground_child_month_ranges(raw, [context]) == raw
