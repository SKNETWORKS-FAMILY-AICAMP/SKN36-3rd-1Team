"""
C1(LLM 보조 청킹) OpenAI 실제 호출 실험 — 문서 1개의 "지정한 창"만 호출한다. (유료 API)

사용 조건 (반드시 확인)
- 사용자가 승인한 범위(문서·창 번호·예산)에서만 실행한다. 2026-11-06 승인: DOC_NHUF_003 창 0, 1회, 예산 상한 $3.
- --confirm-paid-call 이 없으면 모델 접근 확인(GET /models, 과금 없음)과 비용 상한 계산만 하고 생성 호출은 하지 않는다.
- Qdrant·Neo4j·원본 데이터에 쓰지 않는다. 결과 JSON만 이 폴더에 저장한다.
- API 키·프롬프트 전체·원문 전체를 출력·저장하지 않는다 (섹션 시작 줄 앞부분 40자, 해시, 위치만 저장).

실행 (프로젝트 루트, .venv):
    .venv\\Scripts\\python.exe docs/development/experiments/desktop_20261106/c1_openai_window_call.py ^
        --document DOC_NHUF_003 --window 0 --model gpt-5.6-terra --budget 3 --confirm-paid-call
"""

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database import chunking as ck  # noqa: E402
from database import handoff_data as hd  # noqa: E402
from database import llm_chunking as lc  # noqa: E402

OUTPUT_DIR = Path(__file__).resolve().parent


def line_preview(text: str, position: int, limit: int = 40) -> str:
    end = position
    while end < len(text) and text[end] not in "\r\n":
        end += 1
    return text[position:min(end, position + limit)]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--document", required=True)
    parser.add_argument("--window", type=int, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--budget", type=float, default=3.0)
    parser.add_argument("--max-output-tokens", type=int, default=4000)
    parser.add_argument("--reasoning-effort", default="low")
    parser.add_argument("--confirm-paid-call", action="store_true")
    args = parser.parse_args()

    documents = {document["document_id"]: document for document in hd.load_documents(verify_hash=True)}
    document = documents[args.document]
    text = document["text"]
    windows = lc.make_windows(document)
    window = windows[args.window]
    prompt = lc.build_section_prompt(text[window["start"]:window["end"]])

    access = lc.check_openai_model_access(args.model)
    prices = lc.OPENAI_PRICES_PER_1M.get(args.model)
    tracker = lc.CostTracker(prices, args.budget, max_calls=1)
    worst = tracker.worst_case_usd(lc.estimate_input_tokens(prompt), args.max_output_tokens)
    plan = {"document_id": args.document, "window": args.window, "window_count": len(windows),
            "window_range": [window["start"], window["end"]], "prompt_chars": len(prompt),
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "estimated_input_tokens_upper": lc.estimate_input_tokens(prompt),
            "max_output_tokens": args.max_output_tokens, "reasoning_effort": args.reasoning_effort,
            "worst_case_usd": round(worst, 6), "budget_usd": args.budget, "prices_per_1m": prices}
    print(json.dumps({"model_access": access, "plan": plan}, ensure_ascii=False, indent=2))
    if not access["accessible"]:
        print("모델 접근 불가 → 호출하지 않음")
        return 2
    if not args.confirm_paid_call:
        print("--confirm-paid-call 없음 → 생성 호출하지 않음")
        return 0

    call_log: list[dict] = []
    propose = lc.make_openai_section_proposer(
        args.model, tracker=tracker, max_output_tokens=args.max_output_tokens,
        reasoning_effort=args.reasoning_effort, max_retries=1, call_log=call_log)
    chunks, report = lc.chunk_document_llm(document, propose, window_indices={args.window},
                                           model_info={"provider": "openai", "model": args.model,
                                                       "reasoning_effort": args.reasoning_effort})

    # B3와 섹션 시작 비교 (이 창 범위 안)
    in_window = lambda position: window["start"] <= position < window["end"]  # noqa: E731
    llm_starts = {start for start, _t, _l in lc.validate_proposals(document, report.get("proposals_used", []))["starts"]}
    b3_starts = {start for start, _t, _l in (ck._heading_starts_html_b3(text) if document["source_type"] != "pdf"
                                             else ck._heading_starts_pdf(text)) if in_window(start)}
    b3_chunks, _b3_report = ck.chunk_document_b3(document)
    comparison = {
        "common": sorted(llm_starts & b3_starts), "llm_only": sorted(llm_starts - b3_starts),
        "b3_only": sorted(b3_starts - llm_starts),
        "lines": {str(position): line_preview(text, position) for position in sorted(llm_starts | b3_starts)},
        "orphans": {"b3": len(ck.find_orphan_label_chunks(b3_chunks)),
                    "c1": len(ck.find_orphan_label_chunks(chunks)) if not report["used_fallback"] else None},
        "heading_alignment": {"b3": ck.heading_alignment(document, b3_chunks),
                              "c1": ck.heading_alignment(document, chunks)},
        "chunk_counts": {"b3": len(b3_chunks), "c1_or_fallback": len(chunks)},
        "short_under_100": {"b3": sum(len(c["text"]) < 100 for c in b3_chunks),
                            "c1_or_fallback": sum(len(c["text"]) < 100 for c in chunks)},
    }
    result = {
        "run_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "model_access": access, "plan": plan,
        "proposals": [{key: item.get(key) for key in ("title", "start_text", "page")}
                      for item in report.get("proposals_used", [])],
        "used_fallback": report["used_fallback"], "fallback_reasons": report["fallback_reasons"],
        "chunking_version": report["chunking_version"],
        "accepted_sections": [{**item, "line": line_preview(text, item["start"])} for item in report["accepted_sections"]],
        "issues": report["issues"], "coverage": report["coverage"],
        "llm_calls": report["llm_calls"], "api_call_log": call_log, "cost": tracker.summary(),
        "chunks": [{key: chunk[key] for key in ("chunk_id", "char_start", "char_end", "page_start", "section_title")}
                   | {"chars": len(chunk["text"])} for chunk in chunks],
        "b3_comparison": comparison,
        "note": "창 하나만 호출한 결과다. 나머지 창 구간은 LLM 제안 없이 마지막 섹션에 이어진다. 검색 성능 평가 아님.",
    }
    out = OUTPUT_DIR / f"c1_openai_{args.document}_w{args.window}.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {key: result[key] for key in ("used_fallback", "fallback_reasons", "chunking_version", "cost")}
    summary["issues"] = [item["issue"] for item in result["issues"]]
    summary["accepted"] = [(item["title"], item["start"], item["line"]) for item in result["accepted_sections"]]
    summary["b3_comparison"] = {key: comparison[key] for key in ("common", "llm_only", "b3_only")}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"saved: {out.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
