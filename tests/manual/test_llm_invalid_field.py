
"""I. 허용되지 않은 ask_info 필드 처리 검증.

실제 OpenAI API는 호출하지 않는다.
"""

import json

from backend.llm import generator as g


def main():
    contexts = [
        {
            "source_id": 1,
            "policy_id": "POL_TEST_I",
            "policy_name": "가상 청년 지원 정책",
            "text": (
                "본 정책은 가구원 수에 따라 소득 기준이 "
                "달라지며, 구체적인 기준은 모집공고를 따릅니다."
            ),
        }
    ]

    fake_response = {
        "status": "info",
        "answer": (
            "가구원 수에 따른 소득 기준 확인이 필요합니다.[1]"
        ),
        "used_source_ids": [1],
        "follow_up_questions": [
            {
                "kind": "ask_info",
                "text": "가구원 수가 몇 명인가요?",
                "field": "household_size",
            }
        ],
    }

    original_call = g._call_openai

    def fake_call(messages):
        return json.dumps(fake_response, ensure_ascii=False)

    try:
        g._call_openai = fake_call

        result = g.generate_answer(
            question="이 정책에 신청할 수 있나요?",
            contexts=contexts,
            policy_checks=[],
            profile={
                "age": 25,
                "region": "서울특별시",
                "income": 3000000,
            },
        )

    finally:
        g._call_openai = original_call

    print("=" * 60)
    print("I. 허용되지 않은 ask_info 필드 처리 테스트")
    print("=" * 60)

    print(json.dumps(result, ensure_ascii=False, indent=2))

    expected = [
        {
            "kind": "question",
            "text": "가구원 수가 몇 명인가요?",
        }
    ]

    assert result["follow_up_questions"] == expected
    assert result["status"] == "info"
    assert result["used_source_ids"] == [1]

    print("\nPASS: household_size를 question으로 변환")
    print("PASS: LLMOutputError 없이 정상 반환")
    print("PASS: 출처 및 응답 형식 유지")


if __name__ == "__main__":
    main()
