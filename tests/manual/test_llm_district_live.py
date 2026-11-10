
"""G. 시도 정보는 있지만 시군구 확인이 필요한 경우 - 실제 API 테스트."""

import json
import os
import time

from dotenv import load_dotenv

from backend.llm.generator import generate_answer


def main():
    load_dotenv()

    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY가 설정되지 않았습니다.")

    # 사용자 입력: 시도까지만 알고 있음
    profile = {
        "age": 25,
        "region": "서울특별시",
    }

    # 실제 정책이 아닌 테스트용 가상 공식 근거
    contexts = [
        {
            "source_id": 1,
            "policy_id": "POL_TEST_G",
            "policy_name": "강남구 청년 지원 정책 (가상)",
            "text": (
                "본 사업의 신청 대상은 만 19세 이상 "
                "34세 이하의 청년으로, 신청일 기준 "
                "서울특별시 강남구에 주민등록이 되어 "
                "있어야 합니다. 강남구 이외 지역의 "
                "거주자는 신청 대상에서 제외됩니다."
            ),
        }
    ]

    # Checker의 보수적인 조건 판정 결과를 가정
    policy_checks = [
        {
            "policy_id": "POL_TEST_G",
            "conditions": [
                {
                    "field": "age",
                    "label": "나이",
                    "status": "eligible",
                    "reason": (
                        "만 25세는 만 19~34세 "
                        "연령 조건에 포함됩니다."
                    ),
                },
                {
                    "field": "region",
                    "label": "거주 지역",
                    "status": "needs_check",
                    "reason": (
                        "사용자의 서울특별시 거주 정보는 "
                        "입력되었습니다. 그러나 정책에서 "
                        "요구하는 강남구 거주 여부는 "
                        "확인되지 않았습니다. "
                        "지역 조건은 자동으로 확정하지 않습니다."
                    ),
                },
            ],
            "reception_status": "unknown",
        }
    ]

    print("=" * 60)
    print("G. 시군구 추가 확인 테스트")
    print("=" * 60)

    started = time.perf_counter()

    result = generate_answer(
        question=(
            "저는 만 25세이고 서울특별시에 거주합니다. "
            "이 정책을 신청할 수 있나요?"
        ),
        contexts=contexts,
        policy_checks=policy_checks,
        profile=profile,
    )

    elapsed = time.perf_counter() - started

    print("\n[응답 결과]")
    print(json.dumps(result, ensure_ascii=False, indent=2))

    print(f"\n응답 시간: {elapsed:.2f}초")

    print("\n[자동 점검]")

    repeated_region = [
        item
        for item in result["follow_up_questions"]
        if (
            item.get("kind") == "ask_info"
            and item.get("field") == "region"
        )
    ]

    if not repeated_region:
        print("PASS: 입력된 시도 정보를 ask_info로 재질문하지 않음")
    else:
        print("FAIL: 이미 입력된 region을 ask_info로 재질문함")

    if 1 in result["used_source_ids"]:
        print("PASS: 공식 원문 출처 [1] 사용")
    else:
        print("CHECK: 공식 원문 인용 여부 확인 필요")

    print("\n[수동 확인 필요]")
    print("1. 서울특별시를 다시 입력하라고 요구하는가?")
    print("2. 강남구 거주 여부를 추가 확인하도록 안내하는가?")
    print("3. 서울특별시 거주만으로 지역 조건을 충족했다고 단정하는가?")
    print("4. 연령 조건만 충족했는데 전체 신청 가능으로 안내하는가?")
    print("5. 강남구 확인 질문이 answer 또는 question에 적절하게 표현되는가?")


if __name__ == "__main__":
    main()
