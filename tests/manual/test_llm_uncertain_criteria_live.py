
"""H. 프로필이 있지만 정책 기준이 불명확한 경우 - 실제 API 테스트."""

import json
import os
import time

from dotenv import load_dotenv

from backend.llm.generator import generate_answer


def main():
    load_dotenv()

    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY가 설정되지 않았습니다.")

    # 사용자가 이미 제공한 프로필
    profile = {
        "age": 25,
        "region": "서울특별시",
        "income": 3000000,
    }

    # 실제 정책이 아닌 테스트용 가상 근거
    contexts = [
        {
            "source_id": 1,
            "policy_id": "POL_TEST_H",
            "policy_name": "청년 생활 지원 정책 (가상)",
            "text": (
                "본 사업의 신청 대상은 만 19세 이상 "
                "34세 이하의 청년입니다. "
                "거주 요건은 사업별 모집공고에 따릅니다. "
                "소득 요건은 기준연도, 가구원 수, "
                "개인 또는 가구 소득 산정 방식 등 "
                "세부 모집공고에서 정하는 기준을 따릅니다. "
                "현재 제공된 자료에는 구체적인 거주 범위와 "
                "소득 기준 수치가 기재되어 있지 않습니다."
            ),
        }
    ]

    # Checker의 보수적 판정 결과
    policy_checks = [
        {
            "policy_id": "POL_TEST_H",
            "conditions": [
                {
                    "field": "age",
                    "label": "나이",
                    "status": "eligible",
                    "reason": (
                        "만 25세는 만 19~34세 "
                        "연령 범위에 포함됩니다."
                    ),
                },
                {
                    "field": "region",
                    "label": "거주 지역",
                    "status": "needs_check",
                    "reason": (
                        "서울특별시 거주 정보는 입력됐지만 "
                        "정책의 공식 거주 요건이 불명확해 "
                        "자동 판정을 보류합니다."
                    ),
                },
                {
                    "field": "income",
                    "label": "소득",
                    "status": "needs_check",
                    "reason": (
                        "월소득 300만원은 입력됐지만 "
                        "기준연도, 가구원 수 및 소득 산정 "
                        "기준을 확인할 수 없어 "
                        "자동 판정을 보류합니다."
                    ),
                },
            ],
            "reception_status": "unknown",
        }
    ]

    print("=" * 60)
    print("H. 정책 기준 불명확 상황 테스트")
    print("=" * 60)

    started = time.perf_counter()

    result = generate_answer(
        question=(
            "저는 만 25세이고 서울특별시에 거주하며 "
            "월소득은 300만원입니다. "
            "이 청년 지원 정책에 신청할 수 있나요?"
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

    if result["status"] == "info":
        print("PASS: 자격을 확정하지 않고 info 상태 반환")
    else:
        print("CHECK: status가 info가 아님 - 답변 확인 필요")

    repeated = [
        item
        for item in result["follow_up_questions"]
        if (
            item.get("kind") == "ask_info"
            and item.get("field") in profile
        )
    ]

    if not repeated:
        print("PASS: 기존 프로필을 ask_info로 재질문하지 않음")
    else:
        print("FAIL: 이미 입력된 프로필을 ask_info로 재질문")

    if 1 in result["used_source_ids"]:
        print("PASS: 공식 근거 번호 [1] 사용")
    else:
        print("CHECK: 출처 인용 확인 필요")

    print("\n[수동 확인 필요]")
    print("1. 나이·지역·소득을 다시 입력하라고 요구하는가?")
    print("2. needs_check를 정보 미입력으로 잘못 설명하는가?")
    print("3. 공식 소득 기준이 없는데 적격·부적격을 확정하는가?")
    print("4. 연령 조건 충족과 전체 신청 가능 여부를 구분하는가?")
    print("5. 일반 question에 불필요한 중복 질문이 있는가?")


if __name__ == "__main__":
    main()
