"""저장된 지역 문자열의 검색 호환성. 원본 수정·행정코드 해석·자격 판정 없음."""
import re


SIDOS = (
    "서울특별시", "부산광역시", "대구광역시", "인천광역시", "광주광역시", "대전광역시", "울산광역시",
    "세종특별자치시", "경기도", "강원특별자치도", "충청북도", "충청남도", "전북특별자치도",
    "전라남도", "경상북도", "경상남도", "제주특별자치도",
)
ALIASES = dict(zip(("서울", "부산", "대구", "인천", "광주", "대전", "울산", "세종", "경기", "강원",
                    "충북", "충남", "전북", "전남", "경북", "경남", "제주"), SIDOS))
NATIONAL = "전국"
REVIEW_NOTE = "지역 조건 확인 필요: 저장된 지역 문자열은 검색 후보를 찾는 근거이며 실제 지원 자격 충족을 보장하지 않습니다."
_SEPARATOR = re.compile(r"\s*[,，/·|]\s*|\s+및\s+")
_DETAIL = re.compile(r"[가-힣]+(?:시|군|구|읍|면|동|리)")


def _describe(value, *, query=False):
    """완전한 지역 토큰만 읽는다. 이름의 진위·지원 범위는 추론하지 않는다."""
    if not isinstance(value, str) or not value.strip():
        return "unconfirmed", []
    if value.strip() == NATIONAL:
        return "national", []
    parts = _SEPARATOR.split(value.strip())
    components = []
    unresolved = False
    for part in parts:
        tokens = part.split()
        if query and tokens:
            tokens[0] = ALIASES.get(tokens[0], tokens[0])
        if not tokens or tokens[0] not in SIDOS or any(not _DETAIL.fullmatch(t) for t in tokens[1:]):
            unresolved = True
        else:
            components.append((tokens[0], tuple(tokens[1:])))
    kind = "unconfirmed" if unresolved else (
        "exact" if len(components) == 1 and not components[0][1] else "partial")
    return kind, components


def _overlaps(left, right):
    sido, detail = left
    other_sido, other_detail = right
    if sido != other_sido:
        return False
    # 명시된 시군구끼리는 토큰 경계로 비교한다. 동대문구와 강남구를 같은 지역으로 보지 않는다.
    size = min(len(detail), len(other_detail))
    return detail[:size] == other_detail[:size]


def build_region_catalog(payload_rows):
    catalog = {}
    unconfirmed_ids = set()
    for row in payload_rows:
        raw = row.get("region")
        if isinstance(raw, str) and raw.strip():
            if raw not in catalog:
                catalog[raw] = _describe(raw)
            kind, _ = catalog[raw]
        else:
            kind = "unconfirmed"
        if kind == "unconfirmed" and row.get("policy_id"):
            unconfirmed_ids.add(row["policy_id"])
    return sorted(catalog.items()), sorted(unconfirmed_ids)


def plan_region_search(filters, payload_rows=(), *, region_catalog=None):
    """관측된 payload 값만 MatchAny 후보로 만든다. 모호한 값은 별도 검토 경로로 반환."""
    requested = (filters or {}).get("region")
    requested = list(requested) if isinstance(requested, (list, tuple)) else ([] if requested is None else [requested])
    queries = [_describe(value, query=True) for value in requested]
    catalog, unconfirmed_ids = build_region_catalog(payload_rows) if region_catalog is None else region_catalog
    selected = []
    for raw, (kind, components) in catalog:
        if (raw in requested or
            (kind == "national" and any(qkind == "national" or qcomponents for qkind, qcomponents in queries)) or
            any(_overlaps(a, b) for a in components for _, qcomponents in queries for b in qcomponents)):
            selected.append(raw)
    applied = dict(filters or {})
    if requested:
        # 빈 MatchAny는 결과 0개다. 값이 없다고 지역 필터를 없애 전체 검색으로 바꾸지 않는다.
        applied["region"] = selected
    return {
        "requested_values": requested, "filter_values": selected if requested else None,
        "applied_filters": applied or None,
        "unconfirmed_region_values": [raw for raw, (kind, _) in catalog if kind == "unconfirmed"],
        "unconfirmed_policy_ids": list(unconfirmed_ids),
        "unconfirmed_review_route": "지역 필터를 생략하고 policy_id 필터로 별도 검색하여 원문 확인",
        "is_eligibility_decision": False, "note": REVIEW_NOTE,
    }


def describe_hit_region(raw, plan):
    kind, components = _describe(raw)
    if kind == "exact":
        queries = [component for value in plan["requested_values"] for component in _describe(value, query=True)[1]]
        if not plan["requested_values"]:
            kind = "unconfirmed"  # 사용자 지역을 받지 않은 검색을 지역 일치라고 표시하지 않는다.
        elif components[0] not in queries:
            kind = "partial" if any(_overlaps(components[0], query) for query in queries) else "unconfirmed"
    return {
        "original_region": raw, "match_basis": kind,
        "review_required": kind in ("partial", "unconfirmed"),
        "requested_values": plan["requested_values"], "filter_values": plan["filter_values"],
        "filter_applied": bool(plan["requested_values"]),
        "source": "qdrant_payload", "is_eligibility_decision": False,
    }
