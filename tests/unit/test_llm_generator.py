
"""실제 API 호출 없는 LLM 계약 단위 테스트."""

import json

import pytest

from backend.llm import generator as g
from backend.llm.generator import generate_answer


CTX = [
    {
        "source_id": 1,
        "policy_id": "POL_TEST",
        "policy_name": "시험 정책",
        "text": "만 19세 이상 35세 이하 신청 가능",
        "url": "https://example.org",
    }
]

CHECKS = [
    {
        "policy_id": "POL_TEST",
        "conditions": [
            {
                "field": "age",
                "status": "needs_check",
                "label": "나이",
                "reason": "추가 확인",
            }
        ],
        "reception_status": "unknown",
    }
]


def fake_model(monkeypatch, output):
    monkeypatch.setattr(
        g,
        "_call_openai",
        lambda messages: json.dumps(output, ensure_ascii=False),
    )


@pytest.mark.parametrize(
    ("root_status", "one_policy", "corrected"),
    [
        ("ineligible", True, True),
        ("needs_check", True, False),
        ("eligible", True, False),
        ("ineligible", False, False),
    ],
)
@pytest.mark.parametrize(
    ("original", "aligned"),
    [
        (
            "지역과 소득은 추가 확인이 필요해 전체 신청 자격은 확정할 수 없습니다.",
            "지역과 소득은 추가 확인이 필요하지만, 현재 입력 기준으로는 신청 자격을 충족하지 않습니다.",
        ),
        (
            "따라서 **전체 신청 자격은 확정할 수 없습니다** [1].",
            "따라서 **현재 입력 기준으로는 신청 자격을 충족하지 않습니다** [1].",
        ),
        (
            "소득 정보만으로는 전체 신청 자격을 판단할 수 없습니다.",
            "소득 정보만으로는 전체 신청 자격을 판단할 수 없습니다.",
        ),
        (
            "나이를 입력하지 않으면 전체 신청 자격을 확정할 수 없습니다.",
            "나이를 입력하지 않으면 전체 신청 자격을 확정할 수 없습니다.",
        ),
        (
            "다만, 소득 정보를 입력하지 않으면 전체 신청 자격은 확정할 수 없습니다.",
            "다만, 소득 정보를 입력하지 않으면 전체 신청 자격은 확정할 수 없습니다.",
        ),
        # #122 1번 재검증(dev a58c516, 실제 gpt-5.4-mini) GOV042 45세 답변에서 남은 표현.
        (
            "다만 신청 자격은 추가 확인이 필요합니다.",
            "다만 현재 입력 기준으로는 신청 자격을 충족하지 않습니다.",
        ),
        (
            "소득 기준은 중위소득 180% 초과자라는 문구가 있고, 종합 판정도 추가 확인이 필요하다고 되어 있어 "
            "현재 입력만으로는 자격을 확정할 수 없습니다. [1]",
            "소득 기준은 중위소득 180% 초과자라는 문구가 있고, 현재 입력 기준으로는 신청 자격을 충족하지 않습니다. [1]",
        ),
        (
            "소득 자격은 추가 확인이 필요합니다.",
            "소득 자격은 추가 확인이 필요합니다.",
        ),
        (
            "소득 기준은 원문에 있지만, 소득 정보만으로는 자격을 확정할 수 없습니다.",
            "소득 기준은 원문에 있지만, 소득 정보만으로는 자격을 확정할 수 없습니다.",
        ),
        # PR #161 리뷰 필수 4: 원인·조건 절 뒤의 유보는 미입력 때문에 탈락한 것처럼 바꾸지 않는다.
        (
            "나이를 입력하지 않아, 현재 입력만으로는 자격을 확정할 수 없습니다.",
            "나이를 입력하지 않아, 현재 입력만으로는 자격을 확정할 수 없습니다.",
        ),
        (
            "거주 지역 정보가 없어서, 입력만으로는 신청 자격을 확정할 수 없어요.",
            "거주 지역 정보가 없어서, 입력만으로는 신청 자격을 확정할 수 없어요.",
        ),
    ],
)
def test_overall_eligibility_hedge_follows_confirmed_root(
    monkeypatch, root_status, one_policy, corrected, original, aligned,
):
    # PR #148의 GOV042/45세 답변에서 나이와 종합 결론이 엇갈린 부분.
    answer = (
        "만 45세는 연령 대상에 해당하지 않아 나이 기준은 충족하지 않습니다 [1]. "
        f"{original} "
        "필요한 서류는 발달재활서비스 신청서입니다 [1]."
    )
    fake_model(monkeypatch, {
        "status": "answered",
        "answer": answer,
        "used_source_ids": [1],
        "follow_up_questions": [],
    })
    contexts = [{**CTX[0], "policy_id": "POL_GOV_042", "text": "만 18세 미만 대상"}]
    checks = [{
        "policy_id": "POL_GOV_042",
        "conditions": [
            {"field": "age", "status": "ineligible"},
            {"field": "eligibility", "status": "needs_check"},
            {"field": "eligibility", "status": root_status},
            {"field": "region", "status": "needs_check"},
            {"field": "income", "status": "needs_check"},
        ],
    }]
    if not one_policy:
        contexts.append({**CTX[0], "source_id": 2})
        checks.append({"policy_id": "POL_TEST", "conditions": [
            {"field": "eligibility", "status": "needs_check"},
        ]})

    result = generate_answer("발달재활서비스를 신청할 수 있나요?", contexts, checks)

    expected = answer.replace(original, aligned) if corrected else answer
    assert result["answer"] == expected
    assert result["used_source_ids"] == [1]


def test_answer_contract_and_citation(monkeypatch):
    fake_model(
        monkeypatch,
        {
            "status": "answered",
            "answer": "연령 기준은 19~35세입니다 [1].",
            "used_source_ids": [1],
            "follow_up_questions": [],
        },
    )

    result = g.generate_answer("연령 조건은?", CTX, CHECKS)

    assert result["status"] == "answered"
    assert result["used_source_ids"] == [1]


def test_no_evidence_skips_model(monkeypatch):
    monkeypatch.setattr(
        g,
        "_call_openai",
        lambda x: pytest.fail("LLM은 호출하면 안 됨"),
    )

    assert g.generate_answer("질문", [], [])["status"] == "no_evidence"


def test_invalid_citation_rejected(monkeypatch):
    fake_model(
        monkeypatch,
        {
            "status": "answered",
            "answer": "출처 [9]",
            "used_source_ids": [9],
            "follow_up_questions": [],
        },
    )

    with pytest.raises(g.LLMOutputError):
        g.generate_answer("질문", CTX, CHECKS)


def test_missing_citation_rejected(monkeypatch):
    fake_model(
        monkeypatch,
        {
            "status": "answered",
            "answer": "연령 기준입니다.",
            "used_source_ids": [],
            "follow_up_questions": [],
        },
    )

    with pytest.raises(g.LLMOutputError):
        g.generate_answer("질문", CTX, CHECKS)


def test_follow_up_field(monkeypatch):
    fake_model(
        monkeypatch,
        {
            "status": "info",
            "answer": "거주 지역을 알려주세요.",
            "used_source_ids": [],
            "follow_up_questions": [
                {
                    "kind": "ask_info",
                    "text": "거주 지역은?",
                    "field": "region",
                }
            ],
        },
    )

    result = g.generate_answer("질문", CTX, CHECKS)

    assert result["follow_up_questions"][0]["field"] == "region"


def test_invalid_follow_up_field(monkeypatch):
    """허용되지 않은 ask_info 필드는 question 버튼으로 반환하지 않는다."""

    fake_model(
        monkeypatch,
        {
            "status": "info",
            "answer": "정보 필요",
            "used_source_ids": [],
            "follow_up_questions": [
                {
                    "kind": "ask_info",
                    "text": "자녀 나이?",
                    "field": "child_age",
                }
            ],
        },
    )

    result = g.generate_answer("질문", CTX, CHECKS)

    assert result["follow_up_questions"] == []


def test_timeout_propagates(monkeypatch):
    def timeout(messages):
        raise g.LLMTimeoutError("시간 초과")

    monkeypatch.setattr(g, "_call_openai", timeout)

    with pytest.raises(g.LLMTimeoutError):
        g.generate_answer("질문", CTX, CHECKS)


def test_invalid_json(monkeypatch):
    monkeypatch.setattr(
        g,
        "_call_openai",
        lambda messages: "not a json",
    )

    with pytest.raises(g.LLMOutputError):
        g.generate_answer("질문", CTX, CHECKS)


def test_history_trim_and_no_mutation(monkeypatch):
    source = []

    def capture(messages):
        source.append(
            json.loads(messages[1]["content"].split("\n", 1)[1])
        )

        return json.dumps(
            {
                "status": "info",
                "answer": "확인 필요",
                "used_source_ids": [],
                "follow_up_questions": [],
            }
        )

    monkeypatch.setattr(g, "_call_openai", capture)

    histories = [
        {"role": "user", "text": str(i)}
        for i in range(15)
    ]

    original = json.dumps(CTX, ensure_ascii=False)

    g.generate_answer("질문", CTX, CHECKS, histories)

    assert len(source[0]["history_for_conversation_only"]) == 10
    assert json.dumps(CTX, ensure_ascii=False) == original


def test_duplicate_source_ids_rejected(monkeypatch):
    with pytest.raises(ValueError):
        g.generate_answer("질문", CTX + CTX, CHECKS)


def test_used_ids_mismatch_body_is_authoritative(monkeypatch):
    fake_model(
        monkeypatch,
        {
            "status": "answered",
            "answer": "본문 근거 [1]",
            "used_source_ids": [9],
            "follow_up_questions": [],
        },
    )

    result = g.generate_answer("질문", CTX, CHECKS)

    assert result["used_source_ids"] == [1]


@pytest.mark.parametrize(
    "invalid_contexts",
    [None, {}, "", 123],
)
def test_invalid_contexts_rejected(invalid_contexts):
    with pytest.raises(ValueError, match="contexts는 리스트"):
        generate_answer(
            question="청년 지원 정책 알려줘",
            contexts=invalid_contexts,
            policy_checks=[],
        )


def test_empty_contexts_returns_no_evidence():
    result = generate_answer(
        question="청년 지원 정책 알려줘",
        contexts=[],
        policy_checks=[],
    )

    assert result["status"] == "no_evidence"
    assert result["used_source_ids"] == []
    assert result["follow_up_questions"] == []

@pytest.mark.parametrize(
    "question, expected",
    [
        ("신청 서류도 알려드릴까요?", []),
        (
            "신청 서류는 무엇이 필요한가요?",
            [
                {
                    "kind": "question",
                    "text": "신청 서류는 무엇이 필요한가요?",
                }
            ],
        ),
    ],
)
def test_question_button_user_perspective(monkeypatch, question, expected):
    """question 버튼에는 사용자가 AI에게 묻는 질문만 남긴다."""

    fake_model(
        monkeypatch,
        {
            "status": "info",
            "answer": "정책 안내",
            "used_source_ids": [],
            "follow_up_questions": [
                {
                    "kind": "question",
                    "text": question,
                }
            ],
        },
    )

    result = g.generate_answer("신청 관련 문의", CTX, CHECKS)

    assert result["follow_up_questions"] == expected
