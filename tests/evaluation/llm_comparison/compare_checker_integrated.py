"""SSOK: PR #56 후속 — #82. GPT-5.4-nano/mini/5.4 3종을 실제 Checker 판정까지 포함한
run_ask_pipeline end-to-end로 다시 비교한다.

PR #56(compare_models.py/compare_real_gold.py)은 policy_checks=[]로 Checker를 빼고
Generator만 평가했다. 이 스크립트는 같은 5개 실제 질문(RQ027/031/036/040/042)에 대해
실제 검색(HybridSearchSession) → 실제 Checker(check_eligibility) → 실제 Generator를
production과 동일한 run_ask_pipeline 경로로 돌려서, Checker 판정이 섞였을 때도
모델별 결과가 PR #56 결론과 같은지 확인한다.

실행하지 않으면 API를 호출하지 않는다. --execute가 있어야 호출한다.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tests" / "evaluation" / "llm_comparison"))

from compare_models import MODELS, PRICES, calculate_cost  # noqa: E402

QUESTION_IDS = ("RQ027", "RQ031", "RQ036", "RQ040", "RQ042")
QUESTIONS_SOURCE = (
    PROJECT_ROOT / "docs" / "development" / "retrieval_eval" / "questions_candidates_v0.jsonl"
)
OUT_ROOT = PROJECT_ROOT / "tests" / "evaluation" / "llm_comparison" / "results"
CSV_FIELDS = (
    "question_id", "model", "status", "elapsed_seconds", "prompt_tokens",
    "cached_input_tokens", "completion_tokens", "reasoning_tokens",
    "estimated_cost_usd", "num_policy_checks", "num_needs_check_conditions",
    "answer", "citations", "notice", "ask_error",
)
# 빈 프로필(미입력) 기준 — 질문 은행(questions_candidates_v0.jsonl)엔 사용자 프로필이
# 없으므로, 화면에서 아무것도 입력하지 않은 실제 사용자와 같은 조건으로 둔다.
EMPTY_PROFILE = {"age": None, "region": "", "income": None}


def load_questions() -> list[dict]:
    cases = {}
    for line in QUESTIONS_SOURCE.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        case = json.loads(line)
        cases[case["question_id"]] = case
    missing = [qid for qid in QUESTION_IDS if qid not in cases]
    if missing:
        raise ValueError(f"필수 Gold 질문이 누락됐습니다: {missing}")
    return [cases[qid] for qid in QUESTION_IDS]


def run_one(model: str, question_id: str, question: str) -> dict:
    """같은 질문을 세 모델로 돌리되, 검색·Checker 결과는 모델과 무관하므로 매번 새로 조회한다
    (캐시 없이 순수 함수 호출만 관찰하기 위해 — HybridSearchSession 자체 캐시는 그대로 적용됨)."""
    from openai.resources.chat.completions import Completions

    from backend.api.schemas import AskRequest
    from backend.eligibility.checker import check_eligibility
    from backend.llm import generator
    from backend.rag.pipeline import AskError, run_ask_pipeline
    from database.hybrid_search import HybridSearchSession

    original_create = Completions.create
    usage: dict = {}

    def tracked_create(client, *args, **kwargs):
        response = original_create(client, *args, **kwargs)
        token_usage = response.usage
        if token_usage is not None:
            usage["prompt_tokens"] = token_usage.prompt_tokens
            usage["completion_tokens"] = token_usage.completion_tokens
            details = getattr(token_usage, "prompt_tokens_details", None)
            usage["cached_input_tokens"] = getattr(details, "cached_tokens", 0) or 0
            completion_details = getattr(token_usage, "completion_tokens_details", None)
            usage["reasoning_tokens"] = getattr(completion_details, "reasoning_tokens", 0) or 0
        return response

    def search_fn(q, *, filters=None, k=10):
        with HybridSearchSession(profile="b2_06b_v140") as session:
            return session.search_policy_context(q, filters=filters, k=k)

    os.environ["LLM_MODEL"] = model
    request = AskRequest(question=question, profile=EMPTY_PROFILE, history=[])
    started = time.perf_counter()
    status, answer, citations, policy_checks, notice, ask_error = "error", "", [], [], None, ""
    try:
        with patch.object(Completions, "create", tracked_create):
            response = run_ask_pipeline(
                request,
                search_fn=search_fn,
                check_fn=check_eligibility,
                generate_fn=generator.generate_answer,
            )
        status = response.status
        answer = response.answer
        citations = [c.model_dump() for c in response.citations]
        policy_checks = [p.model_dump() for p in response.policy_checks]
        notice = response.notice.model_dump() if response.notice else None
    except AskError as exc:
        ask_error = exc.code
    except Exception as exc:
        ask_error = f"{type(exc).__name__}: {exc}"
    elapsed = round(time.perf_counter() - started, 3)

    needs_check = sum(
        1 for check in policy_checks for cond in check.get("conditions", [])
        if cond.get("status") == "needs_check"
    )
    return {
        "question_id": question_id,
        "model": model,
        "status": status,
        "elapsed_seconds": elapsed,
        "prompt_tokens": usage.get("prompt_tokens", ""),
        "cached_input_tokens": usage.get("cached_input_tokens", ""),
        "completion_tokens": usage.get("completion_tokens", ""),
        "reasoning_tokens": usage.get("reasoning_tokens", ""),
        "estimated_cost_usd": calculate_cost(model, usage),
        "num_policy_checks": len(policy_checks),
        "num_needs_check_conditions": needs_check,
        "answer": answer,
        "citations": citations,
        "notice": notice,
        "ask_error": ask_error,
    }


def save_results(rows: list[dict], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "comparison.csv"
    jsonl_path = out_dir / "comparison.jsonl"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                key: json.dumps(row[key], ensure_ascii=False)
                if isinstance(row[key], (list, dict)) else row[key]
                for key in CSV_FIELDS
            })
    with jsonl_path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="SSOK #82: Checker 연계 LLM 3종 비교")
    parser.add_argument("--budget", type=float, default=0.30, help="누적 추정 비용 상한 USD")
    parser.add_argument("--execute", action="store_true", help="지정해야 실제 API를 호출")
    args = parser.parse_args()

    questions = load_questions()
    print(f"질문 {len(questions)}개 × 모델 {len(MODELS)}개 = 최대 {len(questions) * len(MODELS)}회 API 요청")
    for q in questions:
        print(f"  - {q['question_id']}: {q['question']}")

    if not args.execute:
        print("DRY RUN: API를 호출하지 않았습니다. 실행하려면 --execute를 추가하세요.")
        return

    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
    if not os.getenv("OPENAI_API_KEY"):
        parser.error("OPENAI_API_KEY를 .env 또는 환경변수에 설정해야 합니다.")

    out_dir = OUT_ROOT / f"checker_integrated_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    total = 0.0
    rows: list[dict] = []
    try:
        for q in questions:
            for model in MODELS:
                if total >= args.budget:
                    print(f"예산 제한 도달: ${total:.5f} / ${args.budget:.2f}. 추가 호출 중지")
                    return
                print(f"\n[{model}] {q['question_id']}", flush=True)
                row = run_one(model, q["question_id"], q["question"])
                rows.append(row)
                if row["estimated_cost_usd"] is not None:
                    total += row["estimated_cost_usd"]
                print(
                    f"  status={row['status']}, latency={row['elapsed_seconds']}s, "
                    f"cost=${(row['estimated_cost_usd'] or 0):.6f}, "
                    f"policy_checks={row['num_policy_checks']}, "
                    f"needs_check={row['num_needs_check_conditions']}"
                )
                if row["ask_error"]:
                    print(f"  ERROR: {row['ask_error']}")
    finally:
        save_results(rows, out_dir)
        print(f"\n저장 완료: {out_dir}")
        print(f"추정 사용액: ${total:.6f}")
        print("주의: PR #56과 달리 이번엔 Checker가 실제로 실행된다 — "
              "policy_checks/needs_check 수치로 Checker 연동 자체가 동작했는지 확인하라.")


if __name__ == "__main__":
    main()
