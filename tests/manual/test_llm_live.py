"""OpenAI 실제 호출 수동 테스트.

자동 pytest 수집 또는 모듈 import 시 API를 호출하지 않는다.
프로젝트 루트에서 아래 명령으로만 실행한다:
    python -m tests.manual.test_llm_live
"""

import json


def main() -> None:
    # 수동 실행 때만 .env를 로딩한다. 환경변수 로딩은 generator에서 하지 않는다.
    from dotenv import load_dotenv

    load_dotenv()

    from backend.llm.generator import generate_answer

    question = "만 25세 서울 거주자가 신청할 수 있는 정책인가요?"

    # 실제 정책이 아닌 API 연결 테스트용 가상 데이터
    contexts = [
        {
            "source_id": 1,
            "policy_id": "POL_TEST",
            "policy_name": "테스트 청년 지원 정책",
            "text": "지원 대상은 만 19세 이상 34세 이하의 청년이다.",
            "quality_warnings": [],
        }
    ]

    policy_checks = [
        {
            "policy_id": "POL_TEST",
            "conditions": [
                {
                    "field": "age",
                    "label": "나이",
                    "status": "eligible",
                    "reason": "만 25세로 연령 요건을 충족합니다.",
                },
                {
                    "field": "region",
                    "label": "거주 지역",
                    "status": "needs_check",
                    "reason": "거주 지역 조건이 확인되지 않았습니다.",
                },
            ],
            "reception_status": "unknown",
        }
    ]

    try:
        result = generate_answer(
            question=question,
            contexts=contexts,
            policy_checks=policy_checks,
            history=None,
        )

        print(json.dumps(result, ensure_ascii=False, indent=2))

    except Exception as error:
        print(f"오류 유형: {type(error).__name__}")
        print(f"오류 내용: {error}")


if __name__ == "__main__":
    main()
