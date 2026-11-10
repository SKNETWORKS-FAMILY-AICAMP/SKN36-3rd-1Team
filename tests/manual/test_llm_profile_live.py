
"""F. 이미 입력한 프로필 정보 중복 질문 방지 - 실제 OpenAI 테스트."""

import json
import os
import time

from dotenv import load_dotenv

from backend.llm.generator import generate_answer


def main():
    load_dotenv()

    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY가 설정되지 않았습니다.")

    # 가상 사용자 프로필
    profile = {
        "age": 25,
        "region": "서울특별시",
        "income": 3000000,
    }

    # 실제 정책 데이터가 아닌 테스트용 가상 근거
    contexts = [
        {
            "source_id": 1,
            "policy_id": "POL_TEST_F",
            "policy_name": "청년 지원 정책 (가상)",
            "text": (
                "신청 대상은 만 19세 이상 34세 이하 청년입니다. "
                "거주 요건과 소득 기준은 별도 모집 공고에서 "
                "정하는 세부 기준에 따릅니다."
            ),
        }
    ]

    policy_checks = [
        {
            "policy_id": "POL_TEST_F",
            "conditions": [
                {
                    "field": "age",
                    "label": "나이",
                    "status": "eligible",
                    "reason": "만 25세는 연령 조건을 충족합니다.",
                },
                {
                    "field": "region",
                    "label": "거주 지역",
                    "status": "needs_check",
                    "reason": (
                        "서울특별시 거주 정보는 입력됐지만 "
                        "정책의 세부 거주 요건은 공식 확인이 필요합니다."
                    ),
                },
                {
                    "field": "income",
                    "label": "소득",
                    "status": "needs_check",
                    "reason": (
                        "월소득 300만원은 입력됐지만 "
                        "기준연도와 가구원 수 등 "
                        "공식 소득 비교 기준이 불명확합니다."
                    ),
                },
            ],
            "reception_status": "unknown",
        }
    ]

    print("=" * 60)
    print("F. 프로필 중복 질문 방지 테스트")
    print("=" * 60)

    started = time.perf_counter()

    result = generate_answer(
        question="제가 이 정책에 신청할 수 있나요?",
        contexts=contexts,
        policy_checks=policy_checks,
        profile=profile,
    )

    elapsed = time.perf_counter() - started

    print("\n[응답 결과]")
    print(json.dumps(result, ensure_ascii=False, indent=2))

    print(f"\n응답 시간: {elapsed:.2f}초")

    print("\n[자동 점검]")

    repeated = [
        item
        for item in result["follow_up_questions"]
        if (
            item.get("kind") == "ask_info"
            and item.get("field") in profile
        )
    ]

    if not repeated:
        print("PASS: 입력된 프로필을 ask_info로 재질문하지 않음")
    else:
        print("FAIL: 입력된 프로필을 ask_info로 재질문함")

    print("\n[수동 확인 필요]")
    print("1. 답변 본문에서 나이·지역·소득을 다시 요구하는가?")
    print("2. 일반 question 유형으로 중복 질문이 나오는가?")
    print("3. needs_check를 무조건 정보 미입력으로 해석하는가?")
    print("4. 연령 충족만으로 전체 신청 자격을 확정하는가?")


if __name__ == "__main__":
    main()
