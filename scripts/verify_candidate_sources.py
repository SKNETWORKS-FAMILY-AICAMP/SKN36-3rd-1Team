"""후보 문서의 저장된 본문·해시·원본 경로를 읽기 전용으로 검사한다.

웹 재수집이나 정책 내용의 사실 판단은 수행하지 않는다.
Graph의 Document 노드는 본문을 생략하므로 별도 원본·정제본에서 읽는다.
"""

import argparse
import hashlib
import json
from pathlib import Path
import sys

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))
from scripts.load_candidate_neo4j import read_jsonl


def verify_sources() -> dict:
    """문서 종류별로 실제 본문을 찾아 같은 SHA-256이 나오는지 비교한다."""
    package = BASE_DIR / "data/releases/v1.4.0-candidate"
    processed = {row["document_id"]: row for row in read_jsonl(BASE_DIR / "data/processed/documents.jsonl")}
    gov24 = {row["service_id"]: row for row in read_jsonl(BASE_DIR / "data/staging/official/gov24_crosscheck_services.jsonl")}
    youth = {row["plcyNo"]: row for row in read_jsonl(BASE_DIR / "data/processed/youthcenter_policies.jsonl")}
    documents = read_jsonl(package / "neo4j/nodes_document.jsonl")
    bodies = {}
    missing_raw = []
    mismatched_hashes = []

    def local_path(relative: str) -> Path:
        path = (BASE_DIR / relative).resolve()
        if not path.is_relative_to(BASE_DIR):
            raise ValueError("원본 경로가 저장소 밖을 가리킵니다")
        return path

    for document in documents:
        document_id = document["document_id"]
        raw_path = local_path(document["local_raw_path"])
        if not raw_path.is_file():
            missing_raw.append(document_id)
        if document_id in processed:
            text = processed[document_id]["text"]
        elif document.get("extracted_text_path"):
            text = local_path(document["extracted_text_path"]).read_text(encoding="utf-8").strip()
        elif document["source_type"] == "gov24_api":
            text = gov24[document["official_id"]]["full_text"]
        elif document["source_type"] == "youthcenter_api":
            text = youth[document["official_id"]]["full_text"]
        elif document.get("doc_type") in {"law", "ordinance"}:
            # 조문 파일의 > 줄은 출처 메모이므로, 원래 생성 규칙대로 본문에서 제외한다.
            lines = raw_path.read_text(encoding="utf-8").splitlines()
            text = "\n".join(line for line in lines if not line.startswith(">")).strip()
        else:
            raise ValueError(f"본문을 읽는 규칙이 없는 문서: {document_id}")
        bodies[document_id] = " ".join(text.split())
        if hashlib.sha256(text.encode("utf-8")).hexdigest() != document["content_hash"]:
            mismatched_hashes.append(document_id)

    # 공백·줄바꿈 차이는 허용하되, 청크의 문장 순서와 내용은 본문 안에 있어야 한다.
    chunks = read_jsonl(package / "qdrant/payload_chunks.jsonl")
    unmatched_chunks = [
        row["chunk_id"] for row in chunks
        if " ".join(row["text"].split()) not in bodies[row["document_id"]]
    ]
    return {
        "status": "PASS" if not missing_raw and not mismatched_hashes and not unmatched_chunks else "FAILED",
        "body_hashes_checked": len(documents), "body_hash_mismatches": mismatched_hashes,
        "raw_files_checked": len(documents), "missing_raw_documents": missing_raw,
        "chunks_checked_against_body": len(chunks), "unmatched_chunk_ids": unmatched_chunks,
        "scope": "저장된 본문과의 일치 검사. 원문의 최신성이나 정책 내용의 정확성을 새로 판정한 결과는 아님.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    try:
        result = verify_sources()
    except (ValueError, KeyError, OSError) as error:
        result = {"status": "FAILED", "error_type": type(error).__name__}
    if args.report:
        args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
