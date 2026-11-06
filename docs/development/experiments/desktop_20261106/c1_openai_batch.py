"""
C1(LLM 보조 청킹) OpenAI 배치 실행 — DOC_LH_001·DOC_NHUF_003·DOC_NHUF_005의 남은 창을 호출한다. (유료 API)

사용 조건 (반드시 확인)
- 사용자가 승인한 뒤에만 --confirm-paid-call 로 실행한다. 없으면 계획·비용 상한만 출력한다(dry-run, 호출 없음).
- 이미 성공한 창(결과 파일이 있고 응답이 completed)은 다시 호출하지 않는다. (DOC_NHUF_003 창 0은 2026-11-06 성공)
- 예산: --budget(기본 $3) 에서 이 폴더의 기존 결과 파일 비용 합계를 먼저 뺀다. 남은 창 전체의 "최악 비용"이
  남은 예산을 넘으면 한 번도 호출하지 않는다. 호출 중에도 매 호출 전에 CostTracker가 다시 검사한다.
- 호출 수: 재시도 포함 최대 --max-calls(기본 12 = 창 10 + 재시도 여유 2). 창마다 재시도 최대 1회(429·5xx·연결 오류만).
- 창마다 결과를 바로 파일로 저장한다(중단돼도 성공한 창은 다시 호출하지 않음). 저장 내용: 제안(제목·시작 문자열·페이지),
  모델 응답 JSON 문자열(섹션 목록만), 호출 기록(상태·토큰·비용·응답 id), 창 단위 검증 결과. 키·프롬프트·원문 전체는 저장하지 않는다.
- --reprocess-only: API 없이 저장된 창 결과만으로 문서별 C1 청킹·검증·B3 대체 판단을 다시 한다.
- --simulate: 네트워크 없이 "구조 인식 제목을 돌려주는 가짜 LLM"으로 흐름만 확인한다(결과에 simulated 표시, 별도 폴더 권장).
- Qdrant·Neo4j·원본 데이터에 쓰지 않는다.

문서별 B3 대체 기준
- 문서의 창 중 하나라도 호출 실패·미실행이면 그 문서는 C1을 만들지 않고 B3를 쓴다 (부분 C1은 경계가 빠진 구간이 생기므로).
- 모든 창이 성공해도 chunk_document_with_sections가 검증 부족·원문 보존 실패로 판단하면 B3로 대체한다.

실행 예 (프로젝트 루트, .venv):
    .venv\\Scripts\\python.exe docs/development/experiments/desktop_20261106/c1_openai_batch.py                     # dry-run
    .venv\\Scripts\\python.exe docs/development/experiments/desktop_20261106/c1_openai_batch.py --confirm-paid-call # 승인 후
    .venv\\Scripts\\python.exe docs/development/experiments/desktop_20261106/c1_openai_batch.py --reprocess-only
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

DEFAULT_DIR = Path(__file__).resolve().parent
TARGET_DOCUMENTS = ("DOC_NHUF_003", "DOC_NHUF_005", "DOC_LH_001")
DEFAULT_MODEL = "gpt-5.6-terra"        # 2026-11-06 사용자 지정 모델 (llm_chunking에는 기본 모델을 두지 않는다)


def window_file(directory: Path, document_id: str, window: int) -> Path:
    return directory / f"c1_openai_{document_id}_w{window}.json"


def load_window_result(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def window_succeeded(result: dict | None) -> bool:
    """응답이 completed이고 제안 목록을 받은 창. (첫 호출 파일 형식도 같은 기준으로 읽는다)"""
    if not result:
        return False
    calls = result.get("api_call_log") or []
    completed = any(call.get("status") == 200 and call.get("response_status") == "completed" for call in calls)
    return completed and result.get("status", "success") == "success" and isinstance(result.get("proposals"), list)


def recorded_cost(result: dict | None) -> float:
    if not result:
        return 0.0
    if "cost_usd" in result:      # 실제 사용량 비용 + 사용량을 모르는 시도의 최악 비용 예약
        return float(result["cost_usd"]) + float(result.get("reserved_usd") or 0.0)
    return float((result.get("cost") or {}).get("spent_usd") or 0.0)


def prior_spent(directory: Path) -> float:
    """이 폴더의 모든 창 결과 파일(재검증 파일 제외)에 기록된 실제 비용 합계."""
    total = 0.0
    for path in directory.glob("c1_openai_DOC_*_w*.json"):
        if path.stem.endswith("_revalidated"):
            continue
        total += recorded_cost(load_window_result(path))
    return round(total, 6)


def simulated_proposer(document: dict):
    """네트워크 없는 가짜 LLM: 창 원문 안의 구조 인식 제목을 그대로 돌려준다 (흐름 확인용, 품질 아님)."""
    headings = ck.analyze_structure(document)["headings"]
    markers = ck.find_page_markers(document["text"]) if document["source_type"] == "pdf" else []

    def propose(prompt: str) -> str:
        window_text = prompt.split("공고문:\n", 1)[1][:-1]
        start = document["text"].find(window_text)
        sections = []
        for heading in headings:
            if start <= heading["start"] < start + len(window_text):
                page = max((p for s, _e, p in markers if s <= heading["start"]), default=None)
                sections.append({"title": heading["title"], "start_text": heading["line"][:20], "page": page})
        return json.dumps({"sections": sections}, ensure_ascii=False)
    return propose


def run_window(document: dict, window_index: int, propose, call_log: list, simulated: bool) -> dict:
    windows = lc.make_windows(document)
    window = windows[window_index]
    raw_holder: dict = {}

    def recording(prompt: str) -> str:
        raw = propose(prompt)
        raw_holder["raw"] = raw
        return raw
    before = len(call_log)
    spent_before = propose.tracker.spent_usd if hasattr(propose, "tracker") else 0.0
    reserved_before = propose.tracker.reserved_usd if hasattr(propose, "tracker") else 0.0
    proposals, calls, issues = lc.propose_sections(document, recording, window_indices={window_index})
    validation = lc.validate_proposals(document, proposals)
    status = "failed" if issues else "success"
    return {
        "run_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "simulated": simulated, "status": status,
        "plan": {"document_id": document["document_id"], "window": window_index, "window_count": len(windows),
                 "window_range": [window["start"], window["end"]], "pages": window["pages"]},
        "proposals": [{key: item.get(key) for key in ("title", "start_text", "page")} for item in proposals],
        "raw_response": raw_holder.get("raw"),
        "raw_response_sha256": hashlib.sha256(str(raw_holder.get("raw")).encode("utf-8")).hexdigest()
        if "raw" in raw_holder else None,
        "llm_calls": calls, "api_call_log": call_log[before:] if not simulated else
        [{"status": 200, "response_status": "completed", "simulated": True}] if status == "success" else [],
        "call_issues": issues,
        "cost_usd": round((propose.tracker.spent_usd - spent_before) if hasattr(propose, "tracker") else 0.0, 6),
        "reserved_usd": round((propose.tracker.reserved_usd - reserved_before) if hasattr(propose, "tracker") else 0.0, 6),
        "window_validation": {"accepted": len(validation["accepted"]), "issues": validation["issues"]},
    }


def combine_documents(directory: Path, documents: dict) -> dict:
    """저장된 창 결과로 문서별 C1 청킹·검증·B3 대체 판단 (API 호출 없음)."""
    summary = {}
    for document_id in TARGET_DOCUMENTS:
        document = documents[document_id]
        windows = lc.make_windows(document)
        results = [load_window_result(window_file(directory, document_id, index)) for index in range(len(windows))]
        missing = [index for index, result in enumerate(results) if not window_succeeded(result)]
        proposals = []
        for index, result in enumerate(results):
            if window_succeeded(result):
                start, end = result["plan"]["window_range"]
                proposals += [{**item, "window_start": start, "window_end": end} for item in result["proposals"]]
        if missing:
            chunks, _report = ck.chunk_document_b3(document)
            entry = {"chunking_version": "B3", "used_fallback": True,
                     "fallback_reasons": [f"창 {missing} 호출 실패 또는 미실행"], "issues": []}
        else:
            chunks, report = lc.chunk_document_with_sections(document, proposals)
            entry = {key: report[key] for key in ("chunking_version", "used_fallback", "fallback_reasons", "issues")}
            entry["accepted_sections"] = len(report["accepted_sections"])
            entry["coverage"] = {k: report["coverage"][k] for k in
                                 ("missing_chars", "unintended_overlap_chars", "excluded_in_chunks", "restore_mismatch", "ok")}
        b3_chunks, _ = ck.chunk_document_b3(document)
        entry.update({"windows": len(windows), "missing_windows": missing, "proposals": len(proposals),
                      "chunks": len(chunks), "b3_chunks": len(b3_chunks),
                      "short_under_100": sum(len(c["text"]) < 100 for c in chunks),
                      "b3_short_under_100": sum(len(c["text"]) < 100 for c in b3_chunks),
                      "orphans": len(ck.find_orphan_label_chunks(chunks)),
                      "b3_orphans": len(ck.find_orphan_label_chunks(b3_chunks)),
                      "cost_usd": round(sum(recorded_cost(r) for r in results), 6)})
        summary[document_id] = entry
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--budget", type=float, default=3.0)
    parser.add_argument("--max-calls", type=int, default=12)
    parser.add_argument("--max-output-tokens", type=int, default=4000)
    parser.add_argument("--reasoning-effort", default="low")
    parser.add_argument("--output-dir", default=str(DEFAULT_DIR))
    parser.add_argument("--confirm-paid-call", action="store_true")
    parser.add_argument("--reprocess-only", action="store_true")
    parser.add_argument("--simulate", action="store_true")
    args = parser.parse_args()
    directory = Path(args.output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    documents = {d["document_id"]: d for d in hd.load_documents(verify_hash=True)}

    pending = []
    for document_id in TARGET_DOCUMENTS:
        for index, window in enumerate(lc.make_windows(documents[document_id])):
            if not window_succeeded(load_window_result(window_file(directory, document_id, index))):
                prompt = lc.build_section_prompt(documents[document_id]["text"][window["start"]:window["end"]])
                pending.append((document_id, index, lc.estimate_input_tokens(prompt)))
    prices = lc.OPENAI_PRICES_PER_1M.get(args.model)
    spent = prior_spent(directory)
    remaining = round(args.budget - spent, 6)
    worst_each = [(est * prices["input"] + args.max_output_tokens * prices["output"]) / 1_000_000 for _d, _w, est in pending]
    plan = {"model": args.model, "budget_usd": args.budget, "prior_spent_usd": spent, "remaining_budget_usd": remaining,
            "pending_windows": [f"{d}#w{w}" for d, w, _e in pending], "pending_count": len(pending),
            "max_calls_including_retries": args.max_calls, "max_output_tokens": args.max_output_tokens,
            "worst_case_usd_without_retries": round(sum(worst_each), 6),
            "worst_case_usd_with_all_retries": round(sum(worst_each) + sorted(worst_each, reverse=True)[0]
                                                     * max(0, args.max_calls - len(pending)) if worst_each else 0, 6),
            "prices_per_1m": prices}
    print(json.dumps(plan, ensure_ascii=False, indent=2))

    if args.reprocess_only:
        summary = combine_documents(directory, documents)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        (directory / "c1_openai_batch_summary.json").write_text(
            json.dumps({"plan": plan, "documents": summary, "reprocessed_at": datetime.now().astimezone()
                        .isoformat(timespec="seconds")}, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0
    if not pending:
        print("호출할 창 없음")
        return 0
    if not args.simulate:
        if not args.confirm_paid_call:
            print("--confirm-paid-call 없음 → 호출하지 않음 (dry-run)")
            return 0
        if plan["worst_case_usd_with_all_retries"] > remaining:
            print("최악 비용이 남은 예산을 넘음 → 호출하지 않음")
            return 2
        access = lc.check_openai_model_access(args.model)
        if not access["accessible"]:
            print(f"모델 접근 불가 → 호출하지 않음: {access}")
            return 2

    tracker = lc.CostTracker(prices, remaining, args.max_calls)
    call_log: list = []
    stopped = None
    for document_id, index, _estimate in pending:
        document = documents[document_id]
        if args.simulate:
            propose = simulated_proposer(document)
        else:
            propose = lc.make_openai_section_proposer(
                args.model, tracker=tracker, max_output_tokens=args.max_output_tokens,
                reasoning_effort=args.reasoning_effort, max_retries=1, call_log=call_log)
        result = run_window(document, index, propose, call_log, args.simulate)
        window_file(directory, document_id, index).write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                                              encoding="utf-8")
        print(f"{document_id}#w{index}: {result['status']} proposals={len(result['proposals'])} "
              f"accepted={result['window_validation']['accepted']} cost=${result['cost_usd']:.5f} "
              f"issues={[i.get('error', i.get('issue')) for i in result['call_issues']]}")
        errors = " ".join(str(i.get("error")) for i in result["call_issues"])
        if "BudgetExceededError" in errors or "CallLimitError" in errors:
            stopped = errors
            print("예산·호출 한도 도달 → 남은 창 호출 중단")
            break

    summary = combine_documents(directory, documents)
    total = {"tracker": tracker.summary() if not args.simulate else {"simulated": True},
             "prior_spent_usd": spent, "stopped": stopped}
    (directory / "c1_openai_batch_summary.json").write_text(
        json.dumps({"plan": plan, "documents": summary, "run": total}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"documents": summary, "run": total}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
