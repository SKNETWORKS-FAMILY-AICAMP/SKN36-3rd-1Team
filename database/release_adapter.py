"""
승인된 데이터 릴리스(예: data/releases/v1.4.0-candidate)를 Issue #5 청킹·적재에 쓰기 위한 읽기 전용 어댑터.

릴리스 패키지에는 문서 본문(documents.jsonl)과 정책 단위 메타데이터(policy_metadata.jsonl)가 없다.
이 모듈은 원본 파일을 바꾸지 않고 다음 두 가지만 메모리에서 만든다.

1. 문서 본문: Document 노드(neo4j/nodes_document.jsonl)의 document_id마다 본문을 찾는다.
   찾는 규칙은 데이터 담당의 검증 스크립트 scripts/verify_candidate_sources.py(verify_sources)와 같다.
     - data/processed/documents.jsonl 에 있는 문서 → 그 text
     - extracted_text_path 가 있으면 → 그 파일
     - source_type gov24_api       → data/staging/official/gov24_crosscheck_services.jsonl 의 full_text (service_id)
     - source_type youthcenter_api → data/processed/youthcenter_policies.jsonl 의 full_text (plcyNo)
     - doc_type law/ordinance      → local_raw_path 파일에서 '>'로 시작하는 출처 메모 줄을 뺀 본문
   본문은 그대로 쓰고(생성·재해석 없음), SHA-256이 Document 노드의 content_hash와 같아야 한다.
2. 문서별 메타데이터: 릴리스 qdrant/payload_chunks.jsonl 의 값을 document_id 단위로 모은다.
   정책 단위로 합치지 않는다 (v1.4.0은 한 정책의 문서끼리 life_stages·verification_status 등이 다를 수 있다).

어긋나면 추측하지 않고 ReleaseAdapterError로 멈춘다 (누락·중복·해시 불일치·문서 내 메타데이터 불일치).
공식 정책 내용의 정확성은 판단하지 않는다. 저장된 원천과의 일치만 확인한다.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

# Qdrant payload에 들어가는 정책·문서 메타데이터 필드 (store.build_point_payload와 같은 목록)
METADATA_FIELDS = (
    "policy_name", "category", "region", "life_stages", "target_groups",
    "eligibility_attributes", "verification_status",
)


class ReleaseAdapterError(ValueError):
    """릴리스 패키지를 #5 입력으로 바꿀 수 없음 (원본을 고치지 않고 멈춘다)."""


class DocumentMetadata(dict):
    """
    document_id → 메타데이터. 이 형식이면 청크 메타데이터를 document_id로만 찾는다 (store.metadata_for_chunk).
    없는 문서를 정책 단위 값으로 대신하지 않기 위해 일반 dict(정책 단위)와 형식으로 구분한다.
    """


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with open(path, encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError as error:
                    raise ReleaseAdapterError(f"{path.name} {line_number}번째 줄 JSON 파싱 실패: {error}") from error
    return rows


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def is_release_package(package_dir: Path | str) -> bool:
    """본문 파일(documents.jsonl) 없이 Document 노드와 청크 payload만 있는 릴리스 패키지인가."""
    package = Path(package_dir)
    return (not (package / "documents.jsonl").is_file()
            and (package / "neo4j" / "nodes_document.jsonl").is_file()
            and (package / "qdrant" / "payload_chunks.jsonl").is_file())


def _inside(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ReleaseAdapterError(f"원천 경로가 저장소 밖을 가리킵니다: {relative}")
    return path


def _unique_index(rows: list[dict[str, Any]], key: str, source: str) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for row in rows:
        value = str(row[key])
        if value in index:
            raise ReleaseAdapterError(f"{source}에 {key} 중복: {value}")
        index[value] = row
    return index


def load_release_documents(package_dir: Path | str, project_root: Path | str) -> list[dict[str, Any]]:
    """
    릴리스 Document 노드 순서대로 [{Document 노드 필드..., "text": 본문}] 를 돌려준다.

    검사: Document 노드 document_id 중복 없음, 모든 문서의 본문을 찾음, SHA-256 = content_hash.
    하나라도 어긋나면 해당 document_id 목록과 함께 ReleaseAdapterError.
    """
    package, root = Path(package_dir), Path(project_root)
    nodes = _read_jsonl(package / "neo4j" / "nodes_document.jsonl")
    _unique_index(nodes, "document_id", "nodes_document.jsonl")
    processed = _unique_index(_read_jsonl(root / "data/processed/documents.jsonl"), "document_id",
                              "data/processed/documents.jsonl")
    sources: dict[str, dict[str, dict[str, Any]]] = {}

    def source(name: str, relative: str, key: str) -> dict[str, dict[str, Any]]:
        if name not in sources:
            sources[name] = _unique_index(_read_jsonl(root / relative), key, relative)
        return sources[name]

    documents, missing, mismatched = [], [], []
    for node in nodes:
        document_id = node["document_id"]
        try:
            if document_id in processed:
                text = processed[document_id]["text"]
            elif node.get("extracted_text_path"):
                text = _inside(root, node["extracted_text_path"]).read_text(encoding="utf-8").strip()
            elif node.get("source_type") == "gov24_api":
                text = source("gov24", "data/staging/official/gov24_crosscheck_services.jsonl",
                              "service_id")[str(node["official_id"])]["full_text"]
            elif node.get("source_type") == "youthcenter_api":
                text = source("youth", "data/processed/youthcenter_policies.jsonl",
                              "plcyNo")[str(node["official_id"])]["full_text"]
            elif node.get("doc_type") in {"law", "ordinance"}:
                # 조문 파일의 '>' 줄은 출처 메모이므로 본문에서 뺀다 (verify_candidate_sources와 같은 규칙)
                lines = _inside(root, node["local_raw_path"]).read_text(encoding="utf-8").splitlines()
                text = "\n".join(line for line in lines if not line.startswith(">")).strip()
            else:
                missing.append(f"{document_id}(본문 규칙 없음)")
                continue
        except (KeyError, FileNotFoundError) as error:
            missing.append(f"{document_id}({type(error).__name__}: {error})")
            continue
        if _sha256_text(text) != node.get("content_hash"):
            mismatched.append(document_id)
            continue
        documents.append({**node, "text": text})
    if missing:
        raise ReleaseAdapterError(f"본문을 찾지 못한 문서 {len(missing)}건: {missing[:5]}")
    if mismatched:
        raise ReleaseAdapterError(f"content_hash와 본문이 다른 문서 {len(mismatched)}건: {mismatched[:5]}")
    return documents


def load_release_document_metadata(package_dir: Path | str,
                                   chunks: list[dict[str, Any]] | None = None) -> DocumentMetadata:
    """
    document_id → 메타데이터(METADATA_FIELDS + policy_id). 릴리스 payload_chunks.jsonl 값을 그대로 쓴다.

    검사: 같은 문서의 청크끼리 값이 같다, 청크의 policy_id가 Document 노드의 policy_id와 같다,
          모든 Document에 메타데이터가 있다. 다른 문서의 값으로 채우거나 덮어쓰지 않는다.
    """
    package = Path(package_dir)
    nodes = _unique_index(_read_jsonl(package / "neo4j" / "nodes_document.jsonl"), "document_id",
                          "nodes_document.jsonl")
    if chunks is None:
        chunks = _read_jsonl(package / "qdrant" / "payload_chunks.jsonl")
    metadata: dict[str, dict[str, Any]] = {}
    for chunk in chunks:
        document_id = chunk["document_id"]
        if document_id not in nodes:
            raise ReleaseAdapterError(f"Document 노드에 없는 청크 문서: {document_id} ({chunk['chunk_id']})")
        if chunk["policy_id"] != nodes[document_id]["policy_id"]:
            raise ReleaseAdapterError(f"청크와 Document의 policy_id가 다름: {chunk['chunk_id']}")
        missing_fields = [field for field in METADATA_FIELDS if field not in chunk]
        if missing_fields:
            raise ReleaseAdapterError(f"청크 메타데이터 필드 없음: {chunk['chunk_id']} {missing_fields}")
        values = {"policy_id": chunk["policy_id"], **{field: chunk[field] for field in METADATA_FIELDS}}
        if document_id in metadata and metadata[document_id] != values:
            raise ReleaseAdapterError(f"같은 문서의 청크 메타데이터 불일치: {document_id} (청크 {chunk['chunk_id']})")
        metadata[document_id] = values
    without = sorted(set(nodes) - set(metadata))
    if without:
        raise ReleaseAdapterError(f"청크 메타데이터가 없는 문서 {len(without)}건: {without[:5]}")
    return DocumentMetadata(metadata)
