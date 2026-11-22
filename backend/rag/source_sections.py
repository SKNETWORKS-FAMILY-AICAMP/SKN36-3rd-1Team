"""원문에 명시된 블록만 발췌한다. 조건·날짜 추론과 추가 검색은 하지 않는다."""
import re

from backend.rag.context_builder import valid_source_url

DOCUMENT_LABELS = frozenset({"구비서류", "제출서류"})
REQUIREMENT_LABELS = frozenset({"지원대상", "선정기준", "지원대상 및 선정기준"})
BLOCK = re.compile(r"(?ms)^\[([^\]\r\n]+)\][ \t]*\n(.*?)(?=^\[[^\]\r\n]+\]|\Z)")


def source_sections(contexts: list[dict], labels: frozenset[str]) -> list[dict] | None:
    """입력 순서를 유지하고 같은 문서·같은 본문만 중복 제거한다. chunk 경계의 발췌도 원문 그대로 둔다."""
    rows, seen = [], set()
    for context in contexts:
        document_id, chunk_id = context.get("document_id"), context.get("chunk_id")
        url = context.get("url") or context.get("source_url")
        if not document_id or not chunk_id or not valid_source_url(url):
            continue
        for match in BLOCK.finditer(context.get("text") or ""):
            label, value = match[1].strip(), match[2].strip()
            key = (document_id, label, value)
            if label not in labels or not value or value in {"-", "해당없음", "없음"} or key in seen:
                continue
            seen.add(key)
            rows.append({"label": label, "value": value, "document_id": document_id,
                         "chunk_id": chunk_id, "source_url": url})
    return rows or None


def application_details(contexts: list[dict]) -> dict:
    sections = source_sections(contexts, DOCUMENT_LABELS | REQUIREMENT_LABELS) or []
    documents = [{"text": row["value"], **{key: row[key] for key in ("document_id", "chunk_id", "source_url")}}
                 for row in sections if row["label"] in DOCUMENT_LABELS]
    # 검수된 공식 기준(official_requirements, #76)과 구분해 원문 발췌 필드로만 전달한다.
    requirements = [{"label": row["label"], "text": row["value"],
                     **{key: row[key] for key in ("document_id", "chunk_id", "source_url")}}
                    for row in sections if row["label"] in REQUIREMENT_LABELS]
    return {"required_documents": documents or None, "requirement_excerpts": requirements or None}
