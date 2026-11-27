"""검색 결과에서 인용 가능한 공식 원문을 고른다."""

from collections.abc import Collection
import logging
import re
from urllib.parse import urlsplit

from backend.eligibility.checker import (
    _AGE_RANGE, _has_no_upper_limit, _is_unverified_full_age_range,
    _is_valid_age, _is_valid_age_range,
)
from database.unknown_group_scope import unavailable_group

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


# These conservative rules select evidence only; they never evaluate eligibility.
_AGE_QUESTION = re.compile(r"나이|연령|(?<!\d)\d{1,3}\s*(?:세|살)")
_ELIGIBILITY_QUESTION = re.compile(r"자격|지원\s*대상|신청\s*(?:가능|할\s*수)|지원\s*받을\s*수")
_METHOD_QUESTION = re.compile(r"신청\s*(?:방법|절차)")
_THIRD_PARTY = re.compile(r"자녀|아동|아이|영유아|부모|배우자|보호자|출생|개월|동반인|가족")
_OTHER_PURPOSE = re.compile(r"우선|우대|가점|서류|제출|증빙|발급\s*절차|교육\s*시간|상담|안내\s*서비스")
_EXCLUSION = re.compile(r"(?:지원\s*)?제외\s*(?:대상|기준|조건)|지원\s*제외|지원받을\s*수\s*없")
_INCLUSION = re.compile(r"지원\s*대상|지원\s*자격|신청\s*자격|선정\s*기준|대상자|연령\s*(?:기준|조건)|나이\s*기준")
_SECTION_END = re.compile(r"^(?:\[|#{1,3}\s*)?(?:지원절차|신청(?:방법|절차|기간)?|구비서류|제출서류|지원내용|지원금|문의|우선|우대|혜택|지원대상|지원자격)(?=$|[\s\](:：])")
_SEPARATE_SECTION = re.compile(r"^(?:\[[^\]]+\]|#{1,3}\s+.+|(?:참고사항|유의사항|기타(?:\s*안내|\s*사항)?)(?=$|[\s:：]))")
_TEXT_RANGE = re.compile(r"(?<![\d.])(?:만\s*)?(\d{1,3})\s*세?\s*[~∼～–—-]\s*(?:만\s*)?(\d{1,3})\s*세(?!\d)")
_TEXT_BOUND = re.compile(r"(?<![\d.])(?:만\s*)?(\d{1,3})\s*세\s*(이상|이하|초과|미만)")


def _age_interval(condition: dict) -> tuple[int, int | None] | None:
    """Reuse Checker's supported BETWEEN syntax and sentinel convention."""
    if condition.get("type") != "AGE" or condition.get("operator") != "BETWEEN":
        return None
    if _THIRD_PARTY.search(str(condition.get("name") or "")):
        return None
    match = _AGE_RANGE.fullmatch(str(condition.get("value") or ""))
    if match is None:
        return None
    low, high = map(int, match.groups())
    if not _is_valid_age_range(low, high) or _is_unverified_full_age_range(low, high):
        return None
    return low, None if _has_no_upper_limit(low, high) else high


def _line_interval(line: str) -> tuple[int, int | None] | None:
    """Only complete ranges or up to two unambiguous bounds on one line."""
    ranges = list(_TEXT_RANGE.finditer(line))
    bounds = list(_TEXT_BOUND.finditer(line))
    if ranges:
        if len(ranges) != 1 or bounds:
            return None
        if re.search(r"\d+\s*세", line[:ranges[0].start()] + line[ranges[0].end():]):
            return None
        if re.match(r"\s*(?:이상|이하|초과|미만)", line[ranges[0].end():]):
            return None
        low, high = map(int, ranges[0].groups())
        return (low, high) if _is_valid_age_range(low, high) else None
    if not 1 <= len(bounds) <= 2:
        return None
    if len(re.findall(r"\d+\s*세", line)) != len(bounds):
        return None
    low, high = 0, None
    directions = set()
    for match in bounds:
        value, operator = int(match.group(1)), match.group(2)
        direction = "low" if operator in {"이상", "초과"} else "high"
        if direction in directions or not _is_valid_age(value):
            return None
        directions.add(direction)
        if direction == "low":
            low = value + (operator == "초과")
        else:
            high = value - (operator == "미만")
    if not _is_valid_age(low) or (high is not None and not _is_valid_age_range(low, high)):
        return None
    return low, high


def _has_age_evidence(text: str, interval: tuple[int, int | None], excluded: bool) -> bool:
    """Require matching bounds, applicant subject, and inclusion/exclusion context.

    A section heading may supply context to a bullet on a later line. Unknown
    subjects, mixed age expressions and document/priority sections fail closed.
    """
    section = None
    for raw_line in re.split(r"[\r\n]+|(?<=[.!?。])(?=[^\d])", text):
        line = raw_line.strip().lstrip("○•- ")
        if _SECTION_END.search(line) or _SEPARATE_SECTION.search(line) or _OTHER_PURPOSE.search(line):
            section = None
        if _OTHER_PURPOSE.search(line) or _THIRD_PARTY.search(line):
            section = None
            continue
        if _EXCLUSION.search(line):
            section = True
        elif _INCLUSION.search(line):
            section = False
        if section is excluded and _line_interval(line) == interval:
            # Negation/alternatives make an otherwise matching interval unsafe.
            if not re.search(r"아니|아닌|아닙|아님|않|제외되지|또는|혹은|미해당|불가|못|(?:수|대상).*없|인지|여부|확인\s*필요|일\s*수", line):
                return True
    return False


def _age_conditions(tree: object) -> list[tuple[dict, bool]] | None:
    """Validate the whole tree before using it; malformed/cyclic trees are no-op."""
    found = []
    seen = set()

    def visit(groups, negated=False, depth=0):
        if not isinstance(groups, list):
            return False
        for group in groups:
            if unavailable_group(group, depth) or id(group) in seen:
                return False
            seen.add(id(group))
            logic = group.get("logic")
            if not isinstance(logic, str) or logic not in {"AND", "OR", "NOT", "UNKNOWN"}:
                return False
            # Nested NOT meaning is deliberately unsupported, not recomputed.
            if negated and logic == "NOT":
                return False
            conditions = group.get("conditions", [])
            if not isinstance(conditions, list) or any(not isinstance(c, dict) for c in conditions):
                return False
            children = group.get("groups", [])
            if not isinstance(children, list) or (logic == "NOT" and len(conditions) + len(children) != 1):
                return False
            excluded = negated or logic == "NOT"
            found.extend((c, excluded) for c in conditions if c.get("type") == "AGE")
            if not visit(group.get("groups", []), excluded, depth + 1):
                return False
        return True

    return found if visit(tree) else None


def complete_condition_evidence(
    selected: list[dict], ranked: list[dict], policies: list[dict],
    question: str, profile: dict, *, limit: int = 1,
) -> list[dict]:
    """Append at most one missing AGE evidence from the existing candidates.

    Preserve the original contexts and source IDs. Only already selected policies,
    the condition relationship's exact document, and PASS documents qualify.
    Missing/unsupported data is a no-op. No DB calls or eligibility changes.
    """
    result = list(selected)
    if type(limit) is not int or limit <= 0 or not selected or len(selected) > 5:
        return result
    if not isinstance(question, str) or not isinstance(profile, dict):
        return result
    if _THIRD_PARTY.search(question) or re.search(r"(?:나이|연령).{0,8}(?:무관|상관없|말고)", question):
        return result
    if not (_AGE_QUESTION.search(question) or _ELIGIBILITY_QUESTION.search(question)
            or (_is_valid_age(profile.get("age")) and _METHOD_QUESTION.search(question))):
        return result
    ids = [c.get("source_id") for c in selected if isinstance(c, dict)]
    if any(type(i) is not int for i in ids) or ids != list(range(1, len(selected) + 1)):
        return result
    by_id = {p.get("policy_id"): p for p in policies if isinstance(p, dict) and isinstance(p.get("policy_id"), str)}
    chosen = {c.get("chunk_id") for c in selected}
    for pid in dict.fromkeys(c.get("policy_id") for c in selected):
        policy = by_id.get(pid, {})
        graph = policy.get("graph")
        eligibility = graph.get("eligibility") if isinstance(graph, dict) else None
        checks = policy.get("evidence_document_check")
        if not isinstance(eligibility, dict) or not isinstance(checks, dict):
            continue
        conditions = _age_conditions(eligibility.get("condition_tree"))
        if conditions is None:
            continue
        missing = []
        for condition, excluded in conditions:
            interval = _age_interval(condition)
            relation = condition.get("relationship")
            doc = relation.get("evidence_document_id") if isinstance(relation, dict) else None
            if interval is None or not isinstance(doc, str) or not doc.strip() or checks.get(doc) != "PASS":
                continue
            if not any(c.get("policy_id") == pid and c.get("document_id") == doc
                       and isinstance(c.get("text"), str) and valid_source_url(c.get("url"))
                       and _has_age_evidence(c["text"], interval, excluded) for c in selected):
                missing.append((doc, interval, excluded))
        for candidate in ranked:
            if not isinstance(candidate, dict) or candidate.get("policy_id") != pid:
                continue
            chunk = candidate.get("chunk_id")
            text = candidate.get("text")
            if (not isinstance(chunk, str) or not chunk or chunk in chosen
                    or not isinstance(text, str) or not text.strip() or not valid_source_url(candidate.get("url"))):
                continue
            if any(candidate.get("document_id") == doc and _has_age_evidence(text, interval, excluded)
                   for doc, interval, excluded in missing):
                return result + [{**candidate, "source_id": max(ids) + 1}]
    return result
