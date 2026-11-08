"""GPT-5.4 mini 실제 답변 품질 수동 검증.

기본 pytest에서는 API를 호출하지 않는다.
사용자가 명시적으로 실행할 때만 실제 OpenAI API를 호출한다.
프로젝트 루트 실행 명령:
    uv run --no-project python -m tests.manual.test_llm_quality

데이터는 모두 가상 정책이며, 실제 제도의 신청 자격을 의미하지 않는다.
응답의 사실성·불확실성 표현·추가 질문은 출력 후 사람의 최종 검토가 필요하다.
"""

from __future__ import annotations

import re
from typing import Any


SCENARIOS: list[dict[str, Any]] = [
    {
        "name": "A. 한국어·출처 인용·지역 미확인",
        "question": "만 25세이고 서울에 살면 이 정책에 바로 신청할 수 있나요?",
        "contexts": [
            {
                "source_id": 1,
                "policy_id": "POL_TEST_A",
                "policy_name": "가상 청년 지원 정책 A",
                "text": "지원 연령은 만 19세 이상 34세 이하이다. 거주 지역 및 신청기간은 별도 공고에서 확인한다.",
                "quality_warnings": [],
            }
        ],
        "policy_checks": [
            {
                "policy_id": "POL_TEST_A",
                "conditions": [
                    {"field": "age", "label": "나이", "status": "eligible", "reason": "25세는 19~34세 범위에 포함됩니다."},
                    {"field": "region", "label": "거주 지역", "status": "needs_check", "reason": "공식적인 지역 제한 확인이 필요합니다."},
                ],
                "reception_status": "unknown",
            }
        ],
        "review": "지역·신청기간을 확인하지 않고 신청 가능하다고 단정하지 않는가? 인용 [1]이 원문 주장과 맞는가?",
    },
    {
        "name": "B. 사용자 나이 누락·추가 질문",
        "question": "저도 청년 지원 대상인가요? 제 나이는 아직 말씀드리지 않았어요.",
        "contexts": [
            {
                "source_id": 2,
                "policy_id": "POL_TEST_B",
                "policy_name": "가상 청년 지원 정책 B",
                "text": "지원 대상은 만 19세부터 34세까지이다. 구체적인 신청 자격은 별도 공고에서 확인한다.",
                "quality_warnings": [],
            }
        ],
        "policy_checks": [
            {
                "policy_id": "POL_TEST_B",
                "conditions": [
                    {"field": "age", "label": "나이", "status": "needs_check", "reason": "사용자의 만 나이가 제공되지 않았습니다."}
                ],
                "reception_status": "unknown",
            }
        ],
        "review": "나이 정보가 없는데도 적격으로 확정하지 않는가? 나이를 묻는 후속 질문이 적절한가?",
    },
]


def _print_result(scenario: dict[str, Any], result: dict[str, Any]) -> None:
    answer = result["answer"]
    valid_ids = {ctx["source_id"] for ctx in scenario["contexts"]}
    cited_ids = {int(n) for n in re.findall(r"\[(\d+)\]", answer)}

    print(f"\n=== {scenario['name']} ===")
    print(f"status: {result['status']}")
    print(f"answer: {answer}")
    print(f"used_source_ids: {result['used_source_ids']}")
    print(f"follow_up_questions: {result['follow_up_questions']}")
    print(f"[{'PASS' if bool(re.search(r'[가-힣]', answer)) else 'REVIEW'}] 한국어 포함")
    print(f"[{'PASS' if cited_ids and cited_ids <= valid_ids else 'REVIEW'}] 실제 제공 출처 번호 인용")
    print("[MANUAL REVIEW] " + scenario["review"])
    print("[MANUAL REVIEW] 사용자가 제공하지 않은 사실을 추측하지 않았는가?")


def main() -> None:
    """수동 실행 시에만 .env를 로딩하고 실제 API를 호출한다."""
    from dotenv import load_dotenv

    load_dotenv()

    from backend.llm.generator import generate_answer

    print("실제 OpenAI API를 최대 2회 호출합니다. (API 사용량 발생 가능)")
    for scenario in SCENARIOS:
        result = generate_answer(
            question=scenario["question"],
            contexts=scenario["contexts"],
            policy_checks=scenario["policy_checks"],
        )
        _print_result(scenario, result)

    print("\n수동 검토 항목을 확인한 후 품질 검증 결과를 최종 기록하세요.")


if __name__ == "__main__":
    main()
