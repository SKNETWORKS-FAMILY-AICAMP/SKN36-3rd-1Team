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


def rank_citable_contexts(policies: list[dict]) -> list[dict]:
    """인용 가능한 원문 후보를 한 번 검증·중복 제거하고 정책 균형 순서로 정렬한다.

    상위 정책마다 첫 근거를 하나씩 먼저 두고, 나머지 근거는 청크 순위로 뒤에 둔다.
    `source_id`와 개수 제한은 붙이지 않는다. 최종 선택은 select_contexts()가 한다.
    """
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
    return [item[2] for item in sorted(first, key=lambda item: item[:2])] + rest


def select_contexts(
    ranked: list[dict], max_contexts: int = 5, deprioritized_policy_ids: Collection[str] = (),
) -> list[dict]:
    """rank_citable_contexts() 결과에서 최종 근거를 고르고 `source_id`를 1부터 붙인다.

    `deprioritized_policy_ids`(Checker가 신청 마감으로 판정한 정책)는 삭제하지 않는다.
    다른 정책의 원문을 모두 배정한 뒤 남은 자리에만 넣고, 각 그룹 안의 순서는 기존 순서를 따른다.
    """
    if max_contexts <= 0:
        return []
    later = set(deprioritized_policy_ids)
    # 마감 여부만 기준으로 안정 정렬한다. 마감이 없으면 정책 균형 순서 그대로다.
    selected = sorted(ranked, key=lambda context: context["policy_id"] in later)
    return [{"source_id": index, **context} for index, context in enumerate(selected[:max_contexts], 1)]


def build_contexts(
    policies: list[dict], max_contexts: int = 5, deprioritized_policy_ids: Collection[str] = (),
) -> list[dict]:
    """검색 결과에서 인용 가능한 근거를 고른다(rank_citable_contexts + select_contexts)."""
    if max_contexts <= 0:
        return []
    return select_contexts(rank_citable_contexts(policies), max_contexts, deprioritized_policy_ids)
