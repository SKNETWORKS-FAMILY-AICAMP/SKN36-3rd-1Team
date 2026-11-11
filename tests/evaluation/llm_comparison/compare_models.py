"""SSOK: 동일한 가상 근거로 세 Generator 모델의 응답을 비교한다.

사용 위치: tests/evaluation/llm_comparison/compare_models.py
실행하지 않으면 API를 호출하지 않는다. --execute가 있어야 호출한다.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import runpy
import sys
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT))

MODELS = ("gpt-5.4-nano", "gpt-5.4-mini", "gpt-5.4")
# 공식 API 가격: USD / 1,000,000 tokens. 일반 텍스트, 표준 처리 기준.
PRICES = {
    "gpt-5.4-nano": {"input": 0.20, "cached": 0.02, "output": 1.25},
    "gpt-5.4-mini": {"input": 0.75, "cached": 0.075, "output": 4.50},
    "gpt-5.4": {"input": 2.50, "cached": 0.25, "output": 15.00},
}
CITATION_RE = re.compile(r"\[(\d+)\]")
CSV_FIELDS = (
    "scenario", "model", "status", "elapsed_seconds", "prompt_tokens",
    "cached_input_tokens", "completion_tokens", "reasoning_tokens",
    "estimated_cost_usd", "citation_valid", "answer", "used_source_ids",
    "follow_up_questions", "review_criterion", "error",
)


def load_scenarios() -> list[dict]:
    source = PROJECT_ROOT / "tests" / "manual" / "test_llm_quality.py"
    if not source.is_file():
        raise FileNotFoundError(
            f"평가 데이터가 없습니다: {source}\n"
            "업로드한 test_llm_quality(2).py를 프로젝트의 "
            "tests/manual/test_llm_quality.py로 배치하세요."
        )
    scenarios = runpy.run_path(str(source))["SCENARIOS"]
    if not isinstance(scenarios, list) or not scenarios:
        raise ValueError("SCENARIOS가 비어 있거나 리스트가 아닙니다.")
    return scenarios


def calculate_cost(model: str, usage: dict) -> float | None:
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    cached = usage.get("cached_input_tokens") or 0
    if prompt is None or completion is None:
        return None
    p = PRICES[model]
    return round(
        ((prompt - cached) * p["input"] + cached * p["cached"]
         + completion * p["output"]) / 1_000_000,
        8,
    )


def citation_is_valid(result: dict, scenario: dict) -> bool:
    valid = {context["source_id"] for context in scenario["contexts"]}
    answer = result.get("answer", "")
    ids = {int(s) for s in CITATION_RE.findall(answer)}
    used = result.get("used_source_ids", [])
    if result.get("status") == "answered" and not ids:
        return False
    return ids <= valid and ids == set(used)


def run_one(model: str, scenario: dict) -> dict:
    """원본 Generator를 호출하면서 SDK 응답 usage만 관찰한다."""
    from openai.resources.chat.completions import Completions
    from backend.llm import generator

    original_create = Completions.create
    usage: dict = {}

    def tracked_create(client, *args, **kwargs):
        response = original_create(client, *args, **kwargs)
        token_usage = response.usage
        if token_usage is not None:
            usage["prompt_tokens"] = token_usage.prompt_tokens
            usage["completion_tokens"] = token_usage.completion_tokens
            details = getattr(token_usage, "prompt_tokens_details", None)
            usage["cached_input_tokens"] = (
                getattr(details, "cached_tokens", 0) or 0
            )
            completion_details = getattr(token_usage, "completion_tokens_details", None)
            usage["reasoning_tokens"] = (
                getattr(completion_details, "reasoning_tokens", 0) or 0
            )
        return response

    os.environ["LLM_MODEL"] = model
    started = time.perf_counter()
    result = None
    error = ""
    try:
        with patch.object(Completions, "create", tracked_create):
            result = generator.generate_answer(
                question=scenario["question"],
                contexts=scenario["contexts"],
                policy_checks=scenario["policy_checks"],
                history=None,
            )
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    elapsed = round(time.perf_counter() - started, 3)
    result = result or {}

    return {
        "scenario": scenario["name"],
        "model": model,
        "status": result.get("status", "error"),
        "elapsed_seconds": elapsed,
        "prompt_tokens": usage.get("prompt_tokens", ""),
        "cached_input_tokens": usage.get("cached_input_tokens", ""),
        "completion_tokens": usage.get("completion_tokens", ""),
        "reasoning_tokens": usage.get("reasoning_tokens", ""),
        "estimated_cost_usd": calculate_cost(model, usage),
        "citation_valid": citation_is_valid(result, scenario) if result else False,
        "answer": result.get("answer", ""),
        "used_source_ids": result.get("used_source_ids", []),
        "follow_up_questions": result.get("follow_up_questions", []),
        "review_criterion": scenario["review"],
        "error": error,
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
    parser = argparse.ArgumentParser(description="LifePolicy AI LLM 3종 비교")
    parser.add_argument("--limit", type=int, default=1, help="평가 시나리오 수 (기본 1)")
    parser.add_argument("--budget", type=float, default=1.0, help="실행 중 누적 추정 비용 상한 USD")
    parser.add_argument("--execute", action="store_true", help="지정해야 실제 API를 호출")
    args = parser.parse_args()
    scenarios = load_scenarios()
    if args.limit < 1 or args.budget <= 0:
        parser.error("--limit은 1 이상, --budget은 0보다 커야 합니다.")
    selected = scenarios[:args.limit]
    print(f"시나리오 {len(selected)}개 × 모델 {len(MODELS)}개 = 최대 {len(selected) * len(MODELS)}회 API 요청")
    for scenario in selected:
        print(f"  - {scenario['name']}: {scenario['review']}")

    if not args.execute:
        print("DRY RUN: API를 호출하지 않았습니다. 실행하려면 --execute를 추가하세요.")
        return

    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
    if not os.getenv("OPENAI_API_KEY"):
        parser.error("OPENAI_API_KEY를 .env 또는 환경변수에 설정해야 합니다.")

    out_dir = (PROJECT_ROOT / "tests" / "evaluation" / "llm_comparison"
               / "results" / datetime.now().strftime("%Y%m%d_%H%M%S"))
    total = 0.0
    rows: list[dict] = []
    try:
        for scenario in selected:
            for model in MODELS:
                if total >= args.budget:
                    print(f"예산 제한 도달: ${total:.5f} / ${args.budget:.2f}. 추가 호출 중지")
                    return
                print(f"\n[{model}] {scenario['name']}", flush=True)
                row = run_one(model, scenario)
                rows.append(row)
                if row["estimated_cost_usd"] is not None:
                    total += row["estimated_cost_usd"]
                print(
                    f"  status={row['status']}, latency={row['elapsed_seconds']}s, "
                    f"cost=${(row['estimated_cost_usd'] or 0):.6f}, "
                    f"citation_valid={row['citation_valid']}"
                )
                if row["error"]:
                    print(f"  ERROR: {row['error']}")
    finally:
        save_results(rows, out_dir)
        print(f"\n저장 완료: {out_dir}")
        print(f"추정 사용액: ${total:.6f}")
        print("주의: 예산은 호출 후 추정치이며 마지막 요청이 상한을 초과할 수 있습니다.")
        print("주의: citation_valid는 인용 형식 검증이며 정책 답변 품질 점수는 아닙니다.")
        print("review_criterion을 기준으로 실제 응답을 사람이 검수하세요.")


if __name__ == "__main__":
    main()
