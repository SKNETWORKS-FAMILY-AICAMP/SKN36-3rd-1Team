"""
저장된 C1 OpenAI 응답(제안 목록)을 현재 검증 규칙으로 다시 검증·청킹한다. API를 호출하지 않는다(과금 없음).

입력: c1_openai_<문서>_w<창>.json (c1_openai_window_call.py 결과)
출력: c1_openai_<문서>_w<창>_revalidated.json — 이전 결과와의 차이, B3 비교, 청크별 본문 앞부분(60자)
DB·원본 데이터에 쓰지 않는다. 원문 전체를 출력하지 않는다.

실행 (프로젝트 루트, .venv):
    .venv\\Scripts\\python.exe docs/development/experiments/desktop_20261106/c1_revalidate_saved.py ^
        --input docs/development/experiments/desktop_20261106/c1_openai_DOC_NHUF_003_w0.json
"""

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database import chunking as ck  # noqa: E402
from database import handoff_data as hd  # noqa: E402
from database import llm_chunking as lc  # noqa: E402


def preview(text: str, limit: int = 60) -> str:
    return " ".join(text.split())[:limit]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    args = parser.parse_args()
    source = Path(args.input)
    saved = json.loads(source.read_text(encoding="utf-8"))
    document_id = saved["plan"]["document_id"]
    window_start, window_end = saved["plan"]["window_range"]
    document = {d["document_id"]: d for d in hd.load_documents(verify_hash=True)}[document_id]
    text = document["text"]

    proposals = [{**item, "window_start": window_start, "window_end": window_end} for item in saved["proposals"]]
    validation = lc.validate_proposals(document, proposals)
    chunks, report = lc.chunk_document_with_sections(document, proposals)
    b3_chunks, _ = ck.chunk_document_b3(document)

    llm_starts = {start for start, _t, _l in validation["starts"]}
    b3_starts = {start for start, _t, _l in ck._heading_starts_html_b3(text) if window_start <= start < window_end}
    previous_accepted = {item["start"] for item in saved["accepted_sections"]}
    result = {
        "source": source.name, "document_id": document_id, "window_range": [window_start, window_end],
        "api_called": False,
        "proposals": len(proposals), "accepted": len(validation["accepted"]),
        "previous_accepted": len(previous_accepted),
        "newly_accepted": [item for item in validation["accepted"] if item["start"] not in previous_accepted],
        "issues": validation["issues"],
        "used_fallback": report["used_fallback"], "fallback_reasons": report["fallback_reasons"],
        "coverage": report["coverage"],
        "accepted_sections": [{**item, "line": preview(text[item["start"]:item["start"] + 60], 40)}
                              for item in validation["accepted"]],
        "chunks": [{"chunk_id": c["chunk_id"], "char_start": c["char_start"], "char_end": c["char_end"],
                    "chars": len(c["text"]), "section_title": c["section_title"], "overlap": c["overlap_prev_chars"],
                    "head": preview(c["text"])} for c in chunks],
        "previous_chunks": [{"chunk_id": c["chunk_id"], "section_title": c["section_title"],
                             "char_start": c["char_start"], "char_end": c["char_end"]} for c in saved["chunks"]],
        "b3_comparison": {
            "common": sorted(llm_starts & b3_starts), "llm_only": sorted(llm_starts - b3_starts),
            "b3_only": sorted(b3_starts - llm_starts),
            "lines": {str(p): preview(text[p:p + 60], 40) for p in sorted(llm_starts | b3_starts)},
            "chunk_counts": {"b3": len(b3_chunks), "c1": len(chunks), "c1_previous": len(saved["chunks"])},
            "short_under_100": {"b3": sum(len(c["text"]) < 100 for c in b3_chunks),
                                "c1": sum(len(c["text"]) < 100 for c in chunks)},
            "orphans": {"b3": len(ck.find_orphan_label_chunks(b3_chunks)), "c1": len(ck.find_orphan_label_chunks(chunks))},
            "heading_alignment": {"b3": ck.heading_alignment(document, b3_chunks),
                                  "c1": ck.heading_alignment(document, chunks)},
        },
    }
    out = source.with_name(source.stem + "_revalidated.json")
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("proposals", "accepted", "previous_accepted", "used_fallback")},
                     ensure_ascii=False))
    print("issues:", [(i["index"], i["issue"], i["title"]) for i in result["issues"]])
    print("newly accepted:", [(i["index"], i["title"], i["start"], i["repeated_title"]) for i in result["newly_accepted"]])
    print("coverage:", {k: result["coverage"][k] for k in ("missing_chars", "unintended_overlap_chars",
                                                          "excluded_in_chunks", "restore_mismatch", "ok")})
    for chunk in result["chunks"]:
        print(f"  {chunk['chunk_id'][-4:]} [{chunk['char_start']:>5}-{chunk['char_end']:>5}] {chunk['chars']:>3}자 "
              f"title={chunk['section_title'][:28]!r} | {chunk['head']}")
    comparison = result["b3_comparison"]
    print("B3 vs C1:", {k: comparison[k] for k in ("chunk_counts", "short_under_100", "orphans")})
    print("alignment:", comparison["heading_alignment"])
    print("llm_only:", [comparison["lines"][str(p)] for p in comparison["llm_only"]])
    print("b3_only:", [comparison["lines"][str(p)] for p in comparison["b3_only"]])
    print(f"saved: {out.resolve().relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
