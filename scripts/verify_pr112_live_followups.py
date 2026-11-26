"""Real model + fixed synthetic evidence; no DB/API/UI E2E. Run from repo root."""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="outputs/pr112_live_followups.json")
    args = parser.parse_args()
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env", override=False)
        load_dotenv(ROOT / "backend" / ".env", override=False)
    except ImportError:
        pass
    if not os.getenv("OPENAI_API_KEY"):
        print("OPENAI_API_KEY is missing. Set it in the project environment; never paste it in chat.")
        return 2
    from backend.llm import generator as g
    from backend.eligibility.checker import check_eligibility
    cases = [
        ("income_method_missing", "월 소득을 어디서 확인하나요?",
         {"age": 30, "region": "서울특별시", "income": None, "child_age_months": 24},
         "소득 확인 방법을 설명. 일반 후속 질문을 소득 입력 버튼으로 바꾸지 않음."),
        ("income_method_given", "월 소득을 어디서 확인하나요?",
         {"age": 30, "region": "서울특별시", "income": 300, "child_age_months": 24},
         "소득이 입력돼도 확인 방법 질문을 중복 입력 요청으로 제거하지 않음."),
        ("support_start_missing", "아이 돌봄 지원은 몇 개월부터 시작되나요?",
         {"age": 30, "region": "서울특별시", "income": None, "child_age_months": None},
         "지원 시작 월령을 설명. 개인 월령 입력 요구만으로 대체하지 않음."),
        ("support_start_given", "아이 돌봄 지원은 몇 개월부터 시작되나요?",
         {"age": 30, "region": "서울특별시", "income": 300, "child_age_months": 24},
         "입력 여부와 무관하게 정책 시작 월령 질문에 답변. 근거에 하한이 없으면 추정하지 않음."),
        ("child_missing", "이 지원을 신청할 수 있을까요? 확인에 필요한 정보도 알려 주세요.",
         {"age": 30, "region": "서울특별시", "income": None, "child_age_months": None},
         "미입력 자녀 월령은 필요시 개월 단위로 본문 안내. 신청자 나이 버튼으로 바꾸지 않음."),
        ("child_given", "아이는 24개월이에요. 이 지원의 자녀 조건에 해당하나요?",
         {"age": 30, "region": "서울특별시", "income": None, "child_age_months": 24},
         "이미 입력한 자녀 월령을 본문·버튼에서 재요청하지 않음."),
        ("criteria_missing", "아이는 몇 개월부터 몇 개월까지 신청할 수 있나요?",
         {"age": 30, "region": "서울특별시", "income": None, "child_age_months": None},
         "정책 월령 기준을 답변. 자녀 월령 입력 요청만으로 대체하지 않음."),
        ("criteria_given", "아이는 24개월인데, 이 정책은 몇 개월까지 신청할 수 있나요?",
         {"age": 30, "region": "서울특별시", "income": None, "child_age_months": 24},
         "정책의 24개월 이하 기준을 답변. 정책 기준 질문을 중복 입력으로 제거하지 않음."),
        ("guardian_and_region_missing", "아이는 24개월이에요. 보호자인 제가 신청하려면 어떤 정보를 더 알려드려야 하나요?",
         {"age": None, "region": None, "income": None, "child_age_months": 24},
         "필요한 보호자 나이·지역 요청을 자녀 월령이 있다는 이유로 삭제하지 않음."),
    ]
    contexts = [{
        "source_id": 1, "policy_id": "POL_TEST_PR112", "policy_name": "월령 회귀검증용 가상 정책",
        "text": "회귀검증용 가상 정책이며 실제 복지정책이 아닙니다. 신청자는 만 19세 이상이며 서울특별시에 거주해야 합니다. 자녀 월령은 24개월 이하입니다. 이 자료에 소득 제한은 없습니다. 접수 일정은 확인되지 않았습니다.",
    }]
    tree = {"group_id": "ROOT", "logic": "AND", "conditions": [
        {"type": "AGE", "operator": "BETWEEN", "value": "19~120"},
        {"condition_id": "COND_CHILD_AGE_LTE_24M", "type": "SPECIAL_TARGET", "unit": "개월",
         "operator": "LTE", "value": "24", "name": "자녀 월령 24개월 이하"},
        {"type": "REGION", "name": "서울 거주", "operator": "EQUAL", "value": "서울특별시"},
    ], "groups": []}
    policy = {"policy_id": "POL_TEST_PR112", "graph": {"policy": {},
        "eligibility": {"condition_tree": [tree], "unknown_logic_groups": [], "structure_issues": []}},
        "data_quality": {}}
    records = []
    original_call = g._call_openai
    for name, question, profile, expected in cases:
        captured = []
        def record_real_call(messages):
            content = original_call(messages)
            captured.append(content)
            return content
        g._call_openai = record_real_call
        item = {"case": name, "question": question, "profile": profile, "review_points": expected}
        try:
            check = check_eligibility(profile, policy)
            final = g.generate_answer(question, contexts, [check], profile=profile)
            item.update(final_response=final, execution="completed", manual_verdict="PENDING")
            print(name + ": completed; manual review required")
        except Exception as exc:
            # Do not write SDK exception details, request headers, or secrets.
            item.update(execution="failed", error_type=type(exc).__name__, manual_verdict="PENDING")
            print(name + ": failed (" + type(exc).__name__ + ")")
        finally:
            g._call_openai = original_call
            item["raw_model_responses"] = captured
            records.append(item)
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True)
    report = {"executed_at": datetime.now(timezone.utc).isoformat(),
              "commit": commit.stdout.strip() if commit.returncode == 0 else None,
              "model": os.getenv("LLM_MODEL") or g.DEFAULT_MODEL,
              "scope": "real LLM + actual Checker/Generator + synthetic fixed evidence; no search/DB/API/UI",
              "manual_review_required": True, "cases": records}
    output = Path(args.output)
    output = output if output.is_absolute() else ROOT / output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Saved: " + str(output))
    return 1 if any(x["execution"] != "completed" for x in records) else 0

if __name__ == "__main__":
    raise SystemExit(main())


