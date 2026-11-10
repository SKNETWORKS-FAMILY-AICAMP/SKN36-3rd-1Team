"""검색 결과에서 인용 가능한 공식 원문을 고른다."""

from collections.abc import Collection
import logging
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)


def valid_source_url(value: object) -> bool:
    if not isinstance(value, str) or any(char.isspace() for char in value):
        return False
    try:
        parsed = urlsplit(value)
        return (
            parsed.scheme in {"http", "https"}
            and bool(parsed.hostname)
            and parsed.username is None
            and (parsed.port is None or parsed.port > 0)
        )
    except ValueError:
        return False


def build_contexts(
    policies: list[dict], max_contexts: int = 5, deprioritized_policy_ids: Collection[str] = (),
) -> list[dict]:
    """상위 정책에 원문 하나씩 배정하고 나머지는 청크 순위로 채운다.

    `deprioritized_policy_ids`(Checker가 신청 마감으로 판정한 정책)는 삭제하지 않는다.
    다른 정책의 원문을 모두 배정한 뒤 남은 자리에만 넣고, 각 그룹 안의 순서는 기존 규칙을 따른다.
    """
    if max_contexts <= 0:
        return []
    later = set(deprioritized_policy_ids)

    candidates = []
    for policy in policies:
        graph = policy.get("graph") or {}
        titles = {doc["document_id"]: doc.get("title") for doc in graph.get("documents", [])}
        warnings = (policy.get("data_quality") or {}).get("warnings", [])
        messages = [warning.get("message") if isinstance(warning, dict) else warning for warning in warnings]
        for hit in policy.get("evidence", []):
            content = hit.get("text")
            url = hit.get("source_url")
            if not isinstance(content, str) or not content.strip() or not valid_source_url(url):
                logger.info("인용할 수 없는 검색 청크를 제외했습니다.")
                continue
            page = hit.get("page_start")
            context = {
                "policy_id": policy["policy_id"],
                "policy_name": policy["policy_name"],
                "document_id": hit["document_id"],
                "chunk_id": hit["chunk_id"],
                "title": titles.get(hit["document_id"]) or policy["policy_name"],
                "url": url,
                "page_number": page if type(page) is int and page > 0 else None,
                "text": content.strip(),
                "quality_warnings": [message for message in messages if isinstance(message, str) and message],
            }
            candidates.append((hit["rank"], policy.get("best_rank", hit["rank"]), context))

    seen_chunks = set()
    seen_policies = set()
    first = []
    rest = []
    for rank, best_rank, context in sorted(candidates, key=lambda item: item[0]):
        if context["chunk_id"] in seen_chunks:
            continue
        seen_chunks.add(context["chunk_id"])
        if context["policy_id"] in seen_policies:
            rest.append(context)
        else:
            first.append((best_rank, rank, context))
            seen_policies.add(context["policy_id"])
    ordered = [item[2] for item in sorted(first, key=lambda item: item[:2])] + rest
    # 마감 여부만 기준으로 안정 정렬한다. 마감이 없으면 기존 결과와 같다.
    selected = sorted(ordered, key=lambda context: context["policy_id"] in later)
    return [{"source_id": index, **context} for index, context in enumerate(selected[:max_contexts], 1)]
