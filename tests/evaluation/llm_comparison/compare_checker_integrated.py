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
from copy import deepcopy
import json
import math
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tests" / "evaluation" / "llm_comparison"))

from compare_models import MODELS, PRICES, calculate_cost  # noqa: E402
from collectors.api._common import mask_secrets  # noqa: E402
from scripts.runpod_embed import atomic, file_sha  # noqa: E402

QUESTION_IDS = ("RQ027", "RQ031", "RQ036", "RQ040", "RQ042")
QUESTIONS_SOURCE = (
    PROJECT_ROOT / "docs" / "development" / "retrieval_eval" / "questions_candidates_v0.jsonl"
)
OUT_ROOT = PROJECT_ROOT / "tests" / "evaluation" / "llm_comparison" / "results"
CSV_FIELDS = (
    "question_id", "model", "status", "elapsed_seconds", "prompt_tokens",
    "cached_input_tokens", "completion_tokens", "reasoning_tokens",
    "estimated_cost_usd", "num_policy_checks", "num_needs_check_conditions",
    "answer", "citations", "notice", "ask_error", "sdk_calls", "input_trace", "redacted",
)
# 빈 프로필(미입력) 기준 — 질문 은행(questions_candidates_v0.jsonl)엔 사용자 프로필이
# 없으므로, 화면에서 아무것도 입력하지 않은 실제 사용자와 같은 조건으로 둔다.
EMPTY_PROFILE = {"age": None, "region": "", "income": None}
SEARCH_PROFILE = "b2_06b_v140"
KST = timezone(timedelta(hours=9))


class CheckpointError(RuntimeError):
    """검증 기록을 저장하지 못하면 추가 유료 호출을 중단한다."""


def positive_budget(value: str) -> float:
    budget = float(value)
    if not math.isfinite(budget) or budget <= 0:
        raise argparse.ArgumentTypeError("예산은 유한한 양수여야 합니다.")
    return budget


def safe_record(value):
    """설정된 비밀값을 기존 마스킹 함수로 제거하고 기록 사본에만 적용한다."""
    secrets = [value for name, value in os.environ.items()
               if name.endswith(("_KEY", "_TOKEN", "_PASSWORD", "_SECRET")) and value]
    sensitive_fields = {"api_key", "password", "token", "authorization", "cookie", "secret"}

    def clean(item):
        from pydantic import BaseModel
        if isinstance(item, BaseModel):
            return clean(item.model_dump(mode="json"))
        if isinstance(item, dict):
            return {key: "***" if key.lower() in sensitive_fields else clean(value)
                    for key, value in item.items()}
        if isinstance(item, (list, tuple)):
            return [clean(value) for value in item]
        return mask_secrets(item, *secrets) if isinstance(item, str) else item

    return clean(value)


def run_metadata() -> dict:
    # main이 .env를 읽은 뒤 호출한다. 이 함수는 파일·Git 상태만 읽는다.
    from database import full_index, handoff_data

    files = [
        Path(__file__), QUESTIONS_SOURCE,
        PROJECT_ROOT / "backend/rag/pipeline.py",
        PROJECT_ROOT / "backend/eligibility/checker.py",
        PROJECT_ROOT / "backend/llm/generator.py",
        PROJECT_ROOT / "ai/prompts/system_prompt.py",
        PROJECT_ROOT / "tests/evaluation/llm_comparison/compare_models.py",
    ]
    return {
        "started_at": datetime.now(KST).isoformat(),
        "git": full_index.git_state(),
        "dataset_version": handoff_data.dataset_version(),
        "manifest_lf_sha256": full_index.manifest_sha256(),
        "files_sha256": {str(path.relative_to(PROJECT_ROOT)): file_sha(path) for path in files},
        "search_profile": SEARCH_PROFILE,
        "prices_usd_per_million_tokens": PRICES,
        "measurement": "fresh search session per request; elapsed includes search/checker/generator",
        "budget_mode": "stop before the next request once known cumulative cost reaches the threshold",
    }


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


def run_one(model: str, question_id: str, question: str, *, checkpoint=None) -> dict:
    """요청 전체 시간을 측정한다. 모델별로 다시 검색한 실제 입력은 별도로 기록한다."""
    from openai.resources.chat.completions import Completions

    from backend.api.schemas import AskRequest
    from backend.eligibility.checker import check_eligibility
    from backend.llm import generator
    from backend.rag.pipeline import AskError, run_ask_pipeline
    from database.hybrid_search import HybridSearchSession

    original_create = Completions.create
    usage: dict = {}
    sdk_calls = 0
    trace = {"request": None, "search": None, "generator": None}

    def persist(stage):
        if checkpoint is not None:
            try:
                checkpoint({"question_id": question_id, "model": model, "stage": stage,
                            "sdk_calls": sdk_calls, "usage": usage, "input_trace": trace})
            except Exception as error:
                raise CheckpointError(type(error).__name__) from None

    def tracked_create(client, *args, **kwargs):
        nonlocal sdk_calls
        sdk_calls += 1
        persist("before_sdk")
        response = original_create(client, *args, **kwargs)
        trace["model_output"] = [
            {"content": choice.message.content, "finish_reason": choice.finish_reason}
            for choice in response.choices
        ]
        token_usage = response.usage
        if token_usage is not None:
            usage["prompt_tokens"] = token_usage.prompt_tokens
            usage["completion_tokens"] = token_usage.completion_tokens
            details = getattr(token_usage, "prompt_tokens_details", None)
            usage["cached_input_tokens"] = getattr(details, "cached_tokens", 0) or 0
            completion_details = getattr(token_usage, "completion_tokens_details", None)
            usage["reasoning_tokens"] = getattr(completion_details, "reasoning_tokens", 0) or 0
        persist("model_received")
        return response

    def search_fn(q, *, filters=None, k=10):
        with HybridSearchSession(profile=SEARCH_PROFILE) as session:
            result = session.search_policy_context(q, filters=filters, k=k)
        trace["search"] = deepcopy({"question": q, "filters": filters, "k": k,
                                    "profile": SEARCH_PROFILE, "result": result})
        return result

    def generate_fn(question, contexts, checks, history, *, profile=None):
        trace["generator"] = deepcopy({"question": question, "contexts": contexts,
                                       "policy_checks": checks, "history": history, "profile": profile})
        persist("generator_input")
        return generator.generate_answer(question, contexts, checks, history, profile=profile)

    request = AskRequest(question=question, profile=EMPTY_PROFILE, history=[])
    trace["request"] = request.model_dump()
    started = time.perf_counter()
    status, answer, citations, policy_checks, notice, ask_error = "error", "", [], [], None, ""
    try:
        with patch.dict(os.environ, {"LLM_MODEL": model}), patch.object(Completions, "create", tracked_create):
            response = run_ask_pipeline(
                request,
                search_fn=search_fn,
                check_fn=check_eligibility,
                generate_fn=generate_fn,
            )
        trace["response"] = response.model_dump()
        persist("pipeline_complete")
        status = response.status
        answer = response.answer
        citations = [c.model_dump() for c in response.citations]
        policy_checks = [p.model_dump() for p in response.policy_checks]
        notice = response.notice.model_dump() if response.notice else None
    except CheckpointError:
        raise
    except AskError as exc:
        ask_error = exc.code
        trace["error"] = {"code": exc.code}
    except Exception as exc:
        # 예상 밖 예외의 원문에는 접속 정보가 포함될 수 있다.
        ask_error = type(exc).__name__
        trace["error"] = {"type": type(exc).__name__}
    elapsed = round(time.perf_counter() - started, 3)

    needs_check = sum(
        1 for check in policy_checks for cond in check.get("conditions", [])
        if cond.get("status") == "needs_check"
    )
    cost = calculate_cost(model, usage) if sdk_calls else 0.0
    if cost is not None and (not math.isfinite(cost) or cost < 0):
        cost = None
    return {
        "question_id": question_id,
        "model": model,
        "status": status,
        "elapsed_seconds": elapsed,
        "prompt_tokens": usage.get("prompt_tokens", ""),
        "cached_input_tokens": usage.get("cached_input_tokens", ""),
        "completion_tokens": usage.get("completion_tokens", ""),
        "reasoning_tokens": usage.get("reasoning_tokens", ""),
        "estimated_cost_usd": cost,
        "num_policy_checks": len(policy_checks),
        "num_needs_check_conditions": needs_check,
        "answer": answer,
        "citations": citations,
        "notice": notice,
        "ask_error": ask_error,
        "sdk_calls": sdk_calls,
        "input_trace": trace,
    }


def storage_record(row: dict) -> dict:
    from fastapi.encoders import jsonable_encoder
    normalized = jsonable_encoder(row)
    record = safe_record(normalized)
    record["redacted"] = bool(row.get("redacted")) or record != normalized
    return record


def csv_record(row: dict) -> dict[str, str]:
    """저장과 재개 검증에서 동일한 CSV 표현을 사용한다."""
    return {
        key: json.dumps(row[key], ensure_ascii=False)
        if isinstance(row[key], (list, dict)) else "" if row[key] is None else str(row[key])
        for key in CSV_FIELDS
    }


def save_results(rows: list[dict], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    records = [storage_record(row) for row in rows]
    csv_path = out_dir / "comparison.csv"
    jsonl_path = out_dir / "comparison.jsonl"
    csv_temp = csv_path.with_suffix(".csv.tmp")
    jsonl_temp = jsonl_path.with_suffix(".jsonl.tmp")
    with csv_temp.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in records:
            writer.writerow(csv_record(row))
    with jsonl_temp.open("w", encoding="utf-8") as f:
        for row in records:
            f.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
    jsonl_temp.replace(jsonl_path)
    csv_temp.replace(csv_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="SSOK #82: Checker 연계 LLM 3종 비교")
    parser.add_argument("--budget", type=positive_budget, default=0.30,
                        help="누적 추정 비용이 도달하면 다음 호출 중단(USD). 마지막 호출로 초과 가능")
    parser.add_argument("--execute", action="store_true", help="지정해야 실제 API를 호출")
    parser.add_argument("--resume-dir", type=Path, help="동일 입력·코드의 미완료 실행을 재개")
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

    metadata = run_metadata()
    rows: list[dict] = []
    if args.resume_dir:
        out_dir = args.resume_dir.resolve()
        previous = json.loads((out_dir / "run.json").read_text(encoding="utf-8"))
        for key in ("files_sha256", "manifest_lf_sha256", "search_profile", "prices_usd_per_million_tokens"):
            if previous.get(key) != metadata.get(key):
                parser.error(f"재개 입력 또는 코드가 다릅니다: {key}")
        if previous.get("results_consistent") is not True:
            parser.error("저장 무결성이 확인되지 않은 실행은 자동 재개할 수 없습니다.")
        rows = [json.loads(line) for line in (out_dir / "comparison.jsonl").read_text(encoding="utf-8").splitlines()]
        try:
            with (out_dir / "comparison.csv").open(encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream)
                consistent = reader.fieldnames == list(CSV_FIELDS) and list(reader) == [csv_record(row) for row in rows]
        except (OSError, ValueError, KeyError):
            consistent = False
        if not consistent:
            parser.error("CSV와 JSONL 결과가 일치하지 않습니다. 원본 복구 후 재개하세요.")
        metadata["resumed_from"] = previous.get("started_at")
    else:
        out_dir = OUT_ROOT / f"checker_integrated_{datetime.now(KST).strftime('%Y%m%d_%H%M%S_%f')}"
        out_dir.mkdir(parents=True, exist_ok=False)
    planned = {(q["question_id"], model) for q in questions for model in MODELS}
    completed = {(row["question_id"], row["model"]) for row in rows}
    if len(completed) != len(rows) or not completed <= planned:
        parser.error("재개 결과에 중복되거나 알 수 없는 문항·모델이 있습니다.")
    for checkpoint_path in out_dir.glob("request-*.json"):
        saved = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        if (saved["question_id"], saved["model"]) not in completed and saved["sdk_calls"]:
            parser.error("결과에 없는 SDK 시도가 있습니다. 체크포인트 복구 전 재호출하지 않습니다.")
    total = sum(row["estimated_cost_usd"] for row in rows if row["estimated_cost_usd"] is not None)
    if not math.isfinite(total) or any(row["estimated_cost_usd"] is not None and row["estimated_cost_usd"] < 0 for row in rows):
        parser.error("재개 비용 기록이 올바르지 않습니다.")
    metadata.update({"budget_usd": args.budget, "status": "incomplete", "known_cost_usd": total,
                     "recorded_requests": len(rows), "planned_requests": len(questions) * len(MODELS)})
    atomic(out_dir / "run.json", safe_record(metadata))
    try:
        if any(row["estimated_cost_usd"] is None for row in rows):
            metadata["status"] = "cost_unknown"
            return
        for q in questions:
            for model in MODELS:
                if (q["question_id"], model) in completed:
                    continue
                if total >= args.budget:
                    metadata["status"] = "budget_reached"
                    print(f"예산 제한 도달: ${total:.5f} / ${args.budget:.2f}. 추가 호출 중지")
                    return
                print(f"\n[{model}] {q['question_id']}", flush=True)
                index = questions.index(q) * len(MODELS) + MODELS.index(model)
                row = run_one(model, q["question_id"], q["question"], checkpoint=lambda record: atomic(
                    out_dir / f"request-{index:02d}.json", storage_record(record)))
                rows.append(row)
                if row["estimated_cost_usd"] is not None:
                    total += row["estimated_cost_usd"]
                save_results(rows, out_dir)
                metadata.update({"known_cost_usd": total, "recorded_requests": len(rows)})
                atomic(out_dir / "run.json", safe_record(metadata))
                cost_text = ("확인 불가" if row["estimated_cost_usd"] is None
                             else f"${row['estimated_cost_usd']:.6f}")
                print(
                    f"  status={row['status']}, latency={row['elapsed_seconds']}s, "
                    f"cost={cost_text}, "
                    f"policy_checks={row['num_policy_checks']}, "
                    f"needs_check={row['num_needs_check_conditions']}"
                )
                if row["ask_error"]:
                    print(f"  ERROR: {safe_record(row['ask_error'])}")
                if row["estimated_cost_usd"] is None:
                    metadata["status"] = "cost_unknown"
                    return
        metadata["status"] = ("completed_with_errors" if any(row["status"] == "error" for row in rows)
                              else "completed")
    except KeyboardInterrupt:
        metadata["status"] = "interrupted"
        raise
    except Exception:
        metadata["status"] = "execution_failed"
        raise
    finally:
        try:
            save_results(rows, out_dir)
        except BaseException as error:
            metadata.update({"status": "save_failed", "save_error": type(error).__name__,
                             "results_consistent": False})
            # CSV와 JSONL은 두 파일이므로 함께 원자적으로 교체되지 않는다.
            # 부분 교체 또는 저장 중단 시 확보한 행을 별도 복구 파일에 보존한다.
            atomic(out_dir / "recovery.json", {"rows": [storage_record(row) for row in rows]})
            raise
        else:
            metadata["results_consistent"] = True
        finally:
            saved_pairs = {(row["question_id"], row["model"]) for row in rows}
            pending = []
            for checkpoint_path in out_dir.glob("request-*.json"):
                record = json.loads(checkpoint_path.read_text(encoding="utf-8"))
                if record["sdk_calls"] and (record["question_id"], record["model"]) not in saved_pairs:
                    pending.append({"question_id": record["question_id"], "model": record["model"]})
            metadata.update({"known_cost_usd": total, "recorded_requests": len(rows),
                             "unrecorded_sdk_attempts": pending,
                             "cost_total_complete": not pending and all(row["estimated_cost_usd"] is not None for row in rows),
                             "finished_at": datetime.now(KST).isoformat()})
            atomic(out_dir / "run.json", safe_record(metadata))
        print(f"\n저장 완료: {out_dir}")
        print(f"확인된 추정 사용액: ${total:.6f}; 실행 상태: {metadata['status']}")
        print("주의: PR #56과 달리 이번엔 Checker가 실제로 실행된다 — "
              "policy_checks/needs_check 수치로 Checker 연동 자체가 동작했는지 확인하라.")


if __name__ == "__main__":
    main()
