
"""Generator 출처 번호 정규화 회귀 테스트. API 호출 없음."""

import json

import pytest

from backend.llm import generator as g


def _response(answer, used=None):
    return {
        "status": "answered",
        "answer": answer,
        "used_source_ids": [1] if used is None else used,
        "follow_up_questions": [],
    }


@pytest.mark.parametrize(
    "incoming,expected,source_id",
    [
        ("[1]", "[1]", 1),
        ("[0001]", "[1]", 1),
        ("[0000000001]", "[1]", 1),
        ("[" + "0" * 5000 + "1]", "[1]", 1),
        ("[0012]", "[12]", 12),
    ],
)
def test_body_and_used_ids_are_canonical(
    incoming, expected, source_id
):
    result = g._validate_model_response(
        _response("정책 근거 " + incoming, [source_id]),
        {source_id},
    )

    assert result["answer"] == "정책 근거 " + expected
    assert result["used_source_ids"] == [source_id]


@pytest.mark.parametrize(
    "invalid",
    [
        "[2]",
        "[0002]",
        "[0000]",
        "[" + "9" * 5000 + "]",
    ],
)
def test_unknown_source_id_rejected(invalid):
    with pytest.raises(
        g.LLMOutputError,
        match="제공되지 않은 source_id",
    ):
        g._validate_model_response(
            _response("정책 근거 " + invalid),
            {1},
        )


def test_mixed_citations():
    result = g._validate_model_response(
        _response(
            "A [0002], B [1], C [0002]",
            [1, 2],
        ),
        {1, 2},
    )

    assert result["answer"] == "A [2], B [1], C [2]"
    assert result["used_source_ids"] == [1, 2]


def test_generator_response_also_has_canonical_citation(
    monkeypatch,
):
    monkeypatch.setattr(
        g,
        "_call_openai",
        lambda _messages: json.dumps(
            _response("공식 원문 [0001]"),
            ensure_ascii=False,
        ),
    )

    contexts = [
        {
            "source_id": 1,
            "policy_id": "POL_TEST",
            "policy_name": "가상 정책",
            "text": "공식 원문",
        }
    ]

    result = g.generate_answer(
        "지원 가능한가요?",
        contexts,
        [],
    )

    assert result["answer"] == "공식 원문 [1]"
    assert result["used_source_ids"] == [1]
