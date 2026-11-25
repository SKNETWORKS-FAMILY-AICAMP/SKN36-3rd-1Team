"""
Neo4j 자격 조건 의미 정합성 점검 (읽기 전용) — v1.4.0-candidate 대응 (Issue #102 Phase 1)
원본: 2026-11-06 점검 스크립트(cb55cb7). 결과는 새 파일에 쓴다 (기존 neo4j_semantic_check.json은 건드리지 않는다).

세 수준을 구분한다.
  A. 구조 정합성   : 노드·관계 총 건수와 Policy ID 집합만 재확인 (건수 일치가 DB 내용 전체 일치를 증명하지 않는다)
  B. 인계 의미 정합성: 조건 그래프가 릴리스 인계 원문(문서 텍스트, nodes_policy.eligibility, 문서별
                     eligibility_attributes)의 의미를 맞게 표현하는가
  C. 공식 원문 정합성: 공식 공고를 조회하지 않았으므로 전부 "unverified"

v1.4.0 변경 요점
- 정책 원천: data/processed/policies.jsonl → 릴리스 neo4j/nodes_policy.jsonl (Policy 노드엔 eligibility_attributes 없음)
- eligibility_attributes: hd.load_payload_metadata()의 문서(document_id) 단위 값을 문서별로 그대로 쓴다.
  정책 단위로 합치거나 마지막 문서 값으로 덮어쓰지 않는다. 속성이 없는 문서는 "attribute_absent"이며 '제한 없음'이 아니다.
- 규칙 변경 내용은 결과 JSON의 rule_changes에 남긴다.

규칙
- Neo4j·Qdrant에 쓰지 않는다 (읽기 전용 실행기 open_neo4j_runner + get_policy_graph_context).
- 원문에 명확한 근거가 없으면 조건을 추론하지 않는다. 키워드로 찾은 줄은 "근거 후보"이며 판정은 사람이 확인해야 한다.
- UNKNOWN은 적격·부적격 어느 쪽으로도 확정하지 않는다.

실행 전 검증 (하나라도 다르면 결과 파일을 쓰지 않고 종료 코드 2)
- manifest_version, manifest SHA-256(LF), 정책 249·문서 273, Policy ID 집합(nodes_policy = documents = payload 메타데이터)
- NEO4J_URI가 R3D Verify(bolt://127.0.0.1:56787)인지 연결 전에 확인
- Neo4j 총 노드·관계 1,147 / 2,702 (조회 전·후), DB Policy ID 집합 일치, 모든 정책의 그래프 컨텍스트 존재
자격 증명은 실행자가 환경변수로 넣는다. 이 스크립트는 비밀번호 값을 출력하지 않는다.

실행 (프로젝트 루트, .venv):
    .venv\\Scripts\\python.exe docs/development/experiments/neo4j_semantic_20261106/neo4j_semantic_check.py
결과: 같은 폴더 neo4j_semantic_check_v1_4_0.json (원문은 근거 줄 150자 이내만)
"""

import ast
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database import embedding as em  # noqa: E402
from database import full_index as fi  # noqa: E402
from database import handoff_data as hd  # noqa: E402
from database import hybrid_search as hs  # noqa: E402

OUT = Path(__file__).resolve().parent / "neo4j_semantic_check_v1_4_0.json"

# 실행 대상 고정값 (사용자 확인 완료 기준)
EXPECTED_DATASET_VERSION = "1.4.0-candidate"
EXPECTED_MANIFEST_SHA256 = "b31307223f7334ffb7588d55670318d07d36e3fc18a78f62e8c961fdad04f3da"
EXPECTED_POLICIES = 249
EXPECTED_DOCUMENTS = 273
EXPECTED_NEO4J_URI = "bolt://127.0.0.1:56787"          # R3D Verify 환경
EXPECTED_TOTALS = {"nodes": 1147, "relationships": 2702}
QUERY_POLICY_IDS = "MATCH (p:Policy) RETURN p.policy_id AS policy_id"

INCOME_BANDS = set(hs.INCOME_BAND_CONDITION_IDS)
BAND_MAX = {"COND_INC_중위소득_0_50_": 50, "COND_INC_중위소득_51_75_": 75, "COND_INC_중위소득_76_100_": 100,
            "COND_INC_중위소득_101_200_": 200, "COND_INC_중위소득_200_초과": 10_000}
PATTERNS = {
    "age": re.compile(r"만\s?\d{1,2}\s?세|\d{1,2}\s?세\s?(?:이상|이하|미만|초과)|\d{1,2}\s?[~∼～-]\s?\d{1,2}\s?세"),
    "income": re.compile(r"중위소득\s?\d{1,3}\s?%|연소득|소득인정액|월평균\s?소득|소득\s?\d"),
    "housing": re.compile(r"무주택"),
    "marriage": re.compile(r"혼인|신혼|미혼|결혼"),
    "children": re.compile(r"자녀|출산|임신|영유아|아동"),
    "residence": re.compile(r"주민등록|주소를\s?두|거주하는|거주자"),
    "employment": re.compile(r"재직|미취업|취업준비|구직자|실업|창업|자영업|농업인|어업인|근로자"),
    "exclusion": re.compile(r"제외\s?대상|지원\s?제외|참여\s?제한|제한\s?대상|지원대상에서\s?제외|※\s?제외"),
}
# 자녀·아동 연령 문맥: 이 줄의 연령 표현은 신청자 연령으로 보지 않고 따로 기록한다 (S7)
CHILD_CONTEXT = re.compile(r"자녀|아동|영유아|유아|출생|출산|임신|태아|초등|미취학|영아")
DATA_ERROR_LINE = re.compile(r"원문 0~0세 표기 오류")
AGE_RANGE = re.compile(r"(\d{1,2})\s?세?\s?[~∼～-]\s?(?:만\s?)?(\d{1,2})\s?세")
AGE_VALUE = re.compile(r"^\s*(\d{1,3})\s*~\s*(\d{1,3})\s*$")      # 그래프 AGE 값 "하한~상한" (세)
OPEN_TERMS = ("제한없음", "해당없음", "", "무관")
EMPTY_EXCLUSION = re.compile(r"(?:제한\s?대상|제외\s?대상)\s*[:：]?\s*[-–—]?\s*$")   # "참여제한대상: -" 처럼 내용 없음
ATTR_ABSENT = "attribute_absent"      # 문서에 해당 속성 키가 없음 (≠ 제한 없음)
# 그래프의 자녀 관련 조건 (S10). 혼인 관련은 조건 이름·값에서 찾는다.
CHILD_RELATED_IDS = {"COND_HH_다자녀가구", "COND_HH_한부모가정_조손가정", "COND_SP_한부모가정"}
MARRIAGE_CONDITION = re.compile(r"혼인|신혼|결혼|미혼|부부")

# S10 v2: 측면(혼인·자녀)별 분류. data_quality_flags.S10_RULE과 같아야 한다.
S10_RULE = "S10_v2_unmodeled_incomplete_review"
S10_DIMENSIONS = ("marriage", "children")
# 자격 요건 문장으로 볼 근거 (policy.eligibility 줄은 항상 요건 문장). 그 밖의 키워드 줄은 '단순 언급'
REQUIREMENT_LINE = re.compile(r"대상|자격|요건|조건|신청|지원\s?기준|해당|가구|이하|이상|미만|초과|이내|거주|둔\s|양육")
# 우선순위·행정 절차·증빙: 필수 자격 요건으로 단정하지 않는다 (세부 요건 탐지에서 제외하고 따로 기록)
NON_REQUIREMENT = re.compile(r"우선\s?순위|우선\s?(?:지원|선정)|가점|구비\s?서류|제출\s?서류|증빙")
# 세부 요건 범주 → (탐지 정규식, 그래프 조건 이름·값에서 같은 요건을 찾는 정규식 또는 None=그래프 표현 없음)
S10_DETAILS = {
    "children": {
        "child_count": (re.compile(r"\d\s?(?:명|인)\s?이상|다자녀|둘째|셋째|두\s?자녀|자녀\s?수"), re.compile(r"다자녀")),
        "age_exception": (re.compile(r"예외|다만|단,|초과\s?(?:가능|하더라도|해도)|도래|유예|미숙아|조산|초등학생|취학|재원|"
                                     r"연령반|출생연도|출생신고|출생일\s?기준|이상\s?(?:도|이라도)\s?포함|포함\s?가능"), None),
        "household_registry": (re.compile(r"동일\s?(?:주민등록|세대|등본)|등본|등재|주민등록|국적"),
                               re.compile(r"주민등록|등본|세대원")),
        "family_type": (re.compile(r"한부모|조손|장애|입양|가정양육|양육\s?공백|맞벌이"), re.compile(r"한부모|조손|장애|입양")),
        "pregnancy_alternative": (re.compile(r"임신|임산부|태아|출산\s?예정"), re.compile(r"임신|임산부|출산")),
    },
    "marriage": {
        "marriage_period": (re.compile(r"혼인\s?(?:기간|신고)|\d+\s?년\s?이내|이내(?:에)?\s?혼인"), None),
        "marriage_status": (re.compile(r"예비\s?신혼|결혼\s?예정|사실혼|혼인\s?중|배우자"), None),
    },
}
CHILD_MONTH_RANGE = re.compile(r"(\d{1,3})\s?(?:개월)?\s?[~∼～-]\s?(\d{1,3})\s?개월")
CHILD_MONTHS = re.compile(r"(\d{1,3})\s?개월\s?(미만|이하)")
CHILD_YEARS = re.compile(r"(\d{1,2})\s?세\s?(미만|이하)")

RULE_CHANGES = [
    {"check": "ALL", "change": "정책 원천을 릴리스 nodes_policy.jsonl로, eligibility_attributes를 문서별 payload 메타데이터로 변경",
     "reason": "v1.4.0 Policy 노드엔 eligibility_attributes가 없고 속성은 문서 단위로만 존재 (예: POL_001 LH 문서 vs LAW/FAQ 문서)"},
    {"check": "ALL", "change": "근거 줄(evidence)에 source(document_id 또는 policy.eligibility)를 붙임",
     "reason": "문서별 출처 차이를 보존하기 위해"},
    {"check": "S2/S4/S6", "change": "속성 근거를 문서별 목록(*_by_document)으로 기록. 속성이 없는 문서는 attribute_absent",
     "reason": "다른 문서 속성을 합치거나 덮어쓰지 않고, 속성 부재를 '제한 없음'으로 해석하지 않기 위해"},
    {"check": "S4", "change": "인계 속성 근거 'mismatch'는 속성이 있는 문서가 1개 이상이고 그 값이 모두 제한없음·해당없음 계열일 때만",
     "reason": "이전 규칙은 속성이 없을 때(빈 목록)도 all()이 참이 되어 'mismatch'로 판정했음 → 부재를 제한 없음으로 본 것"},
    {"check": "S6", "change": "NOT 그룹(제외 조건, 예: COND_EXCL_*) 안의 EMPLOYMENT는 '취업 요건 있음'으로 세지 않음. 문서 간 속성이 다르면 'suspected'",
     "reason": "v1.4.0에 EMPLOYMENT 타입 제외 조건이 추가되어 이전 규칙으로는 누락이 가려짐 / 문서 간 불일치는 확정 근거가 아님"},
    {"check": "S7", "change": "그래프 AGE 값은 '하한~상한' 형식만 해석하고 그 밖은 age_values_unparsed에 기록(예외로 중단하지 않음). NOT 그룹 안 AGE는 비교 제외. 자녀·아동 문맥 줄의 연령 범위는 신청자 연령에서 빼고 child_age_ranges로 따로 기록",
     "reason": "이전 규칙은 split('~') 실패 시 중단, 제외 연령(예: 75~120)을 신청 하한으로 오판, 자녀 나이를 신청자 연령으로 오인할 수 있었음. v1.4.0은 자녀 연령을 SPECIAL_TARGET COND_CHILD_*(개월)로 별도 표현"},
    {"check": "S10", "change": (f"{S10_RULE}: 혼인·자녀 측면별로 unmodeled(관련 조건 없음+요건 문장 근거) / "
                                "incomplete(관련 조건 있으나 자녀 수·혼인 기간·연령 예외·등본 등 세부 요건이 그래프에 없음) / "
                                "review(키워드만 있거나 충분성 판단 불가, 또는 세부 요건이 다른 그래프 조건과 겹침) / "
                                "consistent(관련 조건 있고 세부 요건 없음, 요건 문장의 자녀 연령이 그래프 자녀 조건과 모두 일치) / "
                                "no_text로 분류. unmodeled·incomplete·review 측면이 있으면 finding 생성. "
                                "evidence에 incomplete_dimensions·review_dimensions·dimension_assessment 추가"),
     "reason": ("이전 규칙(Phase 1)은 관련 그래프 조건이 하나라도 있으면 충분성을 보지 않고 제외해 finding 자체가 누락됨 "
                "(관련 조건 보유 20개 정책 중 17개). 관련 조건 존재만으로 충분한 모델링으로 보지 않는다. "
                "우선순위·서류·증빙 문구는 필수 요건으로 단정하지 않고 non_requirement_mentions로만 기록")},
]


class GuardError(RuntimeError):
    """실행 전·후 검증 실패. 결과 파일을 쓰지 않는다."""


def require(ok, message: str) -> None:
    if not ok:
        raise GuardError(message)


def is_open_value(value) -> bool:
    """속성값이 '제한 없음/미확인' 계열인가 (예: '제한없음/미확인', '해당없음')."""
    text = str(value)
    return text in OPEN_TERMS or "제한없음" in text or "미확인" in text or "해당없음" in text


def as_list(value) -> list:
    if value is None:
        return []
    return list(value) if isinstance(value, (list, tuple)) else [value]


def iter_lines(sources: list[tuple[str, str]]):
    for source_id, text in sources:
        for match in re.finditer(r"[^\r\n]+", text):
            yield source_id, match.start(), match.group(0).strip()


def lines_with(sources: list[tuple[str, str]], pattern: re.Pattern, limit: int = 2,
               skip: re.Pattern | None = None, only: re.Pattern | None = None) -> list[dict]:
    found = []
    for source_id, start, line in iter_lines(sources):
        if (pattern.search(line) and not DATA_ERROR_LINE.search(line) and not EMPTY_EXCLUSION.search(line)
                and not (skip and skip.search(line)) and (only is None or only.search(line))):
            found.append({"source": source_id, "char_start": start, "line": line[:150]})
            if len(found) >= limit:
                break
    return found


def walk(tree: dict, negated: bool = False) -> list[tuple[dict, bool]]:
    """(그룹, NOT 그룹 아래인지) 목록."""
    negated = negated or tree.get("logic") == "NOT"
    groups = [(tree, negated)]
    for child in tree.get("groups", []):
        groups += walk(child, negated)
    return groups


def parse_attrs(raw) -> dict:
    try:
        value = ast.literal_eval(raw) if isinstance(raw, str) and raw.strip() else (raw or {})
    except (ValueError, SyntaxError):
        return {"_parse_error": str(raw)[:80]}
    return value if isinstance(value, dict) else {"_parse_error": str(raw)[:80]}


def parse_age_value(value) -> tuple[int, int] | None:
    match = AGE_VALUE.match(str(value))
    return (int(match.group(1)), int(match.group(2))) if match else None


def attr_by_document(records: list[dict], key: str) -> list[dict]:
    """문서별 속성값. 문서마다 따로 두고 합치지 않는다."""
    return [{"document_id": r["document_id"], "source_type": r["source_type"], "attribute_source": r["attribute_source"],
             key: r["attributes"][key] if key in r["attributes"] else ATTR_ABSENT} for r in records]


def has_attr(record: dict, key: str) -> bool:
    return bool(as_list(record["attributes"].get(key)))


def text_child_ages(lines: list[dict]) -> list[tuple[str, str]]:
    """요건 문장의 자녀 연령 표현 → (연산자, 개월 값). 'N세 이하'는 (N+1)*12개월 미만으로 본다."""
    ages = set()
    for item in lines:
        line = item["line"]
        for low, high in CHILD_MONTH_RANGE.findall(line):
            ages.add(("BETWEEN", f"{int(low)}~{int(high)}"))
        for months, kind in CHILD_MONTHS.findall(line):
            ages.add(("LT" if kind == "미만" else "LTE", str(int(months))))
        for years, kind in CHILD_YEARS.findall(line):
            ages.add(("LT", str(int(years) * 12 if kind == "미만" else (int(years) + 1) * 12)))
    return sorted(ages)


def assess_s10_dimension(name: str, sources: list[tuple[str, str]], positive: dict[str, dict],
                         related: list[str]) -> dict:
    """
    S10 측면 하나를 분류한다 (자동 의심 후보·검토 대상이며 확정 오류 아님).
    classification: no_text | unmodeled | incomplete | review | consistent
    """
    keyword_lines = lines_with(sources, PATTERNS[name], limit=50)
    if not keyword_lines:
        return {"classification": "no_text", "related_conditions": related}
    requirement = [item for item in keyword_lines
                   if item["source"] == "policy.eligibility" or REQUIREMENT_LINE.search(item["line"])]
    non_requirement = [item for item in requirement if NON_REQUIREMENT.search(item["line"])]
    detail_lines = [item for item in requirement if not NON_REQUIREMENT.search(item["line"])]
    condition_text = {cid: f"{c.get('name') or ''} {c.get('value') or ''}" for cid, c in positive.items()}
    missing, covered = {}, {}
    for category, (pattern, cover) in S10_DETAILS[name].items():
        hits = [item for item in detail_lines if pattern.search(item["line"])]
        if not hits:
            continue
        covering = sorted(cid for cid, text in condition_text.items() if cover is not None and cover.search(text))
        if covering:
            covered[category] = {"lines": hits[:2], "covered_by": covering}
        else:
            missing[category] = hits[:2]
    result = {"related_conditions": related, "keyword_only": not requirement,
              "requirement_lines": requirement[:3], "requirement_line_count": len(requirement),
              "missing_details": missing, "covered_details": covered,
              "non_requirement_mentions": non_requirement[:2]}
    if name == "children":
        result["text_child_ages"] = text_child_ages(detail_lines)
        result["graph_child_ages"] = sorted({(str(c.get("operator") or "").upper(), str(c.get("value") or "").strip())
                                             for cid, c in positive.items() if cid.startswith("COND_CHILD_")})
    if not requirement:
        result["classification"] = "review"            # 단순 키워드 언급: 자격 요건인지 판단 불가 → 보호 유지
    elif not related:
        result["classification"] = "unmodeled"
    elif missing:
        result["classification"] = "incomplete"
    elif covered:
        result["classification"] = "review"            # 다른 그래프 조건과 겹침: 정의 일치 미확인
    elif (name == "children" and result["text_child_ages"]
          and set(result["text_child_ages"]) <= set(result["graph_child_ages"])):
        result["classification"] = "consistent"
    else:
        result["classification"] = "review"
    return result


def load_dataset():
    """릴리스 데이터 검증 후 (정책, 문서, 메타데이터, manifest 정보). 어긋나면 GuardError."""
    manifest = json.loads(hd.MANIFEST_PATH.read_text(encoding="utf-8"))
    version = manifest.get("manifest_version")
    require(version == EXPECTED_DATASET_VERSION == hd.RELEASE_V140_VERSION,
            f"manifest_version 불일치: {version!r} (기대 {EXPECTED_DATASET_VERSION}). {hd.DATASET_DIR_ENV} 확인")
    manifest_sha = fi.manifest_sha256()
    require(manifest_sha == EXPECTED_MANIFEST_SHA256, f"manifest SHA-256 불일치: {manifest_sha}")

    policies: dict[str, dict] = {}
    for row in hd.read_jsonl(hd.NEO4J_HANDOFF_DIR / "nodes_policy.jsonl"):
        require(row["policy_id"] not in policies, f"nodes_policy policy_id 중복: {row['policy_id']}")
        policies[row["policy_id"]] = row
    require(len(policies) == EXPECTED_POLICIES, f"nodes_policy 정책 수 {len(policies)} ≠ {EXPECTED_POLICIES}")

    documents = hd.load_documents(verify_hash=True)
    document_ids = [d["document_id"] for d in documents]
    require(len(document_ids) == len(set(document_ids)) == EXPECTED_DOCUMENTS,
            f"문서 수 {len(document_ids)}(고유 {len(set(document_ids))}) ≠ {EXPECTED_DOCUMENTS}")
    metadata = hd.load_payload_metadata()
    require(set(metadata) == set(document_ids), "payload 메타데이터 document_id 집합이 문서 집합과 다름")
    for document in documents:
        require(metadata[document["document_id"]]["policy_id"] == document["policy_id"],
                f"문서·메타데이터 policy_id 불일치: {document['document_id']}")
    document_policy_ids = {d["policy_id"] for d in documents}
    missing = sorted(set(policies) - document_policy_ids)
    extra = sorted(document_policy_ids - set(policies))
    require(not missing and not extra, f"정책 ID 집합 불일치: 문서 없는 정책 {missing[:5]}…({len(missing)}), "
                                       f"nodes_policy에 없는 정책 {extra[:5]}…({len(extra)})")
    return policies, documents, metadata, version, manifest_sha


def check_neo4j_target() -> None:
    """연결 전에 대상 DB를 확인한다. 값(특히 비밀번호)은 출력하지 않는다."""
    em.load_env_file()      # override=False: 이미 설정된 환경변수가 우선. 실행기와 같은 값을 본다
    require(os.environ.get("NEO4J_URI") == EXPECTED_NEO4J_URI,
            f"NEO4J_URI가 R3D Verify 대상({EXPECTED_NEO4J_URI})과 다름 → 연결하지 않고 종료 (현재 값은 출력하지 않음)")
    require(os.environ.get("NEO4J_USER") and os.environ.get("NEO4J_PASSWORD"),
            "NEO4J_USER 또는 NEO4J_PASSWORD 미설정 (값은 출력하지 않음)")


def read_totals(runner) -> dict:
    row = runner.read_rows(hs.lg.QUERY_TOTAL_COUNTS)[0]
    return {"nodes": int(row["nodes"]), "relationships": int(row["relationships"])}


def read_graph(policies: dict[str, dict]):
    check_neo4j_target()
    with hs.open_neo4j_runner() as runner:      # ReadOnlyTimedRunner: 읽기 세션, 쓰기 메서드는 PermissionError
        counts_before = read_totals(runner)
        require(counts_before == EXPECTED_TOTALS, f"Neo4j 총 건수 {counts_before} ≠ {EXPECTED_TOTALS} (조회 전)")
        db_ids = [row["policy_id"] for row in runner.read_rows(QUERY_POLICY_IDS)]
        missing = sorted(set(policies) - set(db_ids))
        extra = sorted(set(db_ids) - set(policies))
        require(len(db_ids) == len(set(db_ids)) and not missing and not extra,
                f"DB Policy ID 집합 불일치: 누락 {missing[:5]}({len(missing)}), 추가 {extra[:5]}({len(extra)}), "
                f"중복 {len(db_ids) - len(set(db_ids))}")
        contexts = {}
        ids = sorted(policies)
        for start in range(0, len(ids), 50):
            contexts.update(hs.get_policy_graph_context(ids[start:start + 50], runner=runner)["policies"])
        no_context = sorted(set(policies) - set(contexts))
        require(not no_context, f"그래프 컨텍스트 없는 정책 {no_context[:5]}({len(no_context)})")
        region_names = {row["name"] for row in runner.read_rows("MATCH (r:Region) RETURN r.name AS name")}
        counts_after = read_totals(runner)
        require(counts_after == EXPECTED_TOTALS, f"Neo4j 총 건수 {counts_after} ≠ {EXPECTED_TOTALS} (조회 후)")
    return contexts, region_names, counts_before, counts_after


def main() -> int:
    try:
        policies, documents, metadata, dataset_version, manifest_sha = load_dataset()
        contexts, region_names, counts_before, counts_after = read_graph(policies)
    except GuardError as error:
        print(f"[중단] 실행 전·후 검증 실패: {error}\n결과 파일을 쓰지 않았습니다.", file=sys.stderr)
        return 2

    docs_by_policy: dict[str, list[dict]] = {}
    for document in documents:
        docs_by_policy.setdefault(document["policy_id"], []).append(document)

    findings: list[dict] = []
    review_status = Counter()
    not_groups = []
    age_values_unparsed = []

    def add(policy_id, check, severity, match, evidence, graph, wrong, action):
        findings.append({"policy_id": policy_id, "policy_name": str(policies[policy_id].get("policy_name") or "")[:60],
                         "document_ids": [d["document_id"] for d in docs_by_policy.get(policy_id, [])],
                         "check": check, "severity": severity, "handoff_semantic_match": match,
                         "evidence": evidence, "graph": graph, "wrong_recommendation_example": wrong,
                         "recommended_action": action,
                         "levels": {"A_structure": "counts_and_policy_ids_only", "B_handoff_semantic": match,
                                    "C_official_source": "unverified"}})

    for policy_id, policy in sorted(policies.items()):
        context = contexts[policy_id]
        trees = context["eligibility"]["condition_tree"]
        pairs = [pair for tree in trees for pair in walk(tree)]
        groups = [g for g, _negated in pairs]
        conditions = {c["condition_id"]: c for g in groups for c in g.get("conditions", [])}
        positive = {c["condition_id"]: c for g, negated in pairs if not negated for c in g.get("conditions", [])}
        negated_ids = sorted({c["condition_id"] for g, negated in pairs if negated for c in g.get("conditions", [])})
        for g in groups:
            for c in g.get("conditions", []):
                review_status[(c.get("relationship") or {}).get("review_status")] += 1
            if g.get("logic") == "NOT":
                not_groups.append({"policy_id": policy_id, "group_id": g["group_id"],
                                   "conditions": [c["name"] for c in g.get("conditions", [])], "description": g.get("description")})

        docs = docs_by_policy.get(policy_id, [])
        sources = [(d["document_id"], d.get("text") or "") for d in docs]
        sources.append(("policy.eligibility", str(policy.get("eligibility") or "")))
        text = "\n".join(t for _s, t in sources)
        evidence_by = {name: lines_with(sources, pattern) for name, pattern in PATTERNS.items()}
        evidence_by["age"] = lines_with(sources, PATTERNS["age"], skip=CHILD_CONTEXT)        # 신청자 연령 후보
        child_age_lines = lines_with(sources, PATTERNS["age"], only=CHILD_CONTEXT)
        records = []
        for d in docs:
            attributes = parse_attrs(metadata[d["document_id"]].get("eligibility_attributes"))
            records.append({"document_id": d["document_id"], "source_type": d.get("source_type"),
                            "attribute_source": attributes.get("source"), "attributes": attributes})
        root_logic = trees[0]["logic"] if trees else None
        graph_summary = {"root_logic": root_logic, "groups": [(g["group_id"], g.get("logic")) for g in groups],
                         "conditions": sorted(conditions), "negated_conditions": negated_ids}

        # S1 UNKNOWN 루트 + 조건 0개
        if root_logic == "UNKNOWN" and not conditions:
            present = [k for k in ("age", "income", "housing", "residence", "marriage", "children", "employment") if evidence_by[k]]
            if present:
                severity = "high" if set(present) & {"age", "income", "housing", "residence"} else "medium"
                add(policy_id, "S1_UNKNOWN_WITH_CONDITION_TEXT", severity, "not_modeled",
                    {k: evidence_by[k][:1] for k in present}, graph_summary,
                    "UNKNOWN을 '제한 없음'으로 처리하면 원문 조건(" + ",".join(present) + ")에 맞지 않는 사용자도 적격으로 안내",
                    "UNKNOWN은 판정 보류 → 원문 근거 제시 + 추가 질문. 조건 구조화는 데이터 담당 확인 후")

        # S2 소득 5구간 전부 OR
        for g in groups:
            ids_in = {c["condition_id"] for c in g.get("conditions", [])}
            if g.get("logic") == "OR" and INCOME_BANDS <= ids_in:
                specific_docs = [r["document_id"] for r in records
                                 if any("%" in str(v) and "무관" not in str(v) for v in as_list(r["attributes"].get("income_level")))]
                # 원문에 소득 기준이 있으면 원문과 불일치, 원문엔 없고 어떤 문서 속성이 상한을 말하면 인계 데이터 내부 불일치
                match = ("mismatch" if evidence_by["income"] else
                         "internal_inconsistency" if specific_docs else "suspected")
                add(policy_id, "S2_INCOME_ALL_BANDS_OR", "high" if match != "suspected" else "medium", match,
                    {"eligibility_attributes.income_level_by_document": attr_by_document(records, "income_level"),
                     "documents_with_specific_income_attr": specific_docs, "text": evidence_by["income"][:1]},
                    {"group_id": g["group_id"], "logic": "OR", "income_bands": sorted(ids_in & INCOME_BANDS)},
                    "모든 소득 구간이 OR → 소득과 무관하게 통과. 원문·속성에 소득 상한이 있으면 고소득자도 적격으로 안내",
                    "소득 조건으로 쓰지 말 것. 원문 소득 기준(있다면)을 근거로 판정 보류·추가 질문. 생성 규칙 수정은 데이터 담당")

        # S3 소득 일부 구간 OR → 상한과 원문 수치 비교
        for g in groups:
            ids_in = {c["condition_id"] for c in g.get("conditions", [])} & INCOME_BANDS
            if g.get("logic") == "OR" and ids_in and ids_in != INCOME_BANDS:
                graph_max = max(BAND_MAX[i] for i in ids_in)
                text_pcts = [int(n) for n in re.findall(r"중위소득\s?(\d{1,3})\s?%", text)]
                if text_pcts and graph_max not in text_pcts and not any(p <= graph_max for p in text_pcts):
                    add(policy_id, "S3_INCOME_BAND_MISMATCH", "high", "mismatch",
                        {"text_median_income_pcts": sorted(set(text_pcts)), "text": evidence_by["income"][:1]},
                        {"group_id": g["group_id"], "bands": sorted(ids_in), "graph_upper_pct": graph_max},
                        f"그래프 상한 {graph_max}%와 원문 수치가 달라 소득 판정이 어긋남", "원문 수치 기준으로 데이터 담당 확인")

        # S4 무주택 필수 승격 (루트 AND에 무주택 + 가구유형 OR 그룹)
        for tree in trees:
            direct = {c["condition_id"] for c in tree.get("conditions", [])}
            hh_or = [g for g in tree.get("groups", []) if g.get("logic") == "OR"
                     and any(str(c["condition_id"]).startswith("COND_HH_") for c in g.get("conditions", []))]
            if tree.get("logic") == "AND" and hs.HOMELESS_CONDITION_ID in direct and hh_or:
                with_attr = [r for r in records if has_attr(r, "housing_status")]
                open_docs = [r["document_id"] for r in with_attr
                             if all(is_open_value(v) for v in as_list(r["attributes"]["housing_status"]))]
                # 원문에 무주택이 있으면 필수 여부만 의심. 원문에 없고, 속성이 있는 문서가 모두 '해당없음' 계열이면 불일치.
                # 속성이 있는 문서가 없으면(부재) 제한 없음으로 보지 않고 'suspected'
                match = "suspected" if evidence_by["housing"] else (
                    "mismatch" if with_attr and len(open_docs) == len(with_attr) else "suspected")
                add(policy_id, "S4_HOMELESS_REQUIRED_PROMOTED", "high", match,
                    {"text_무주택": evidence_by["housing"][:1],
                     "eligibility_attributes.housing_status_by_document": attr_by_document(records, "housing_status"),
                     "documents_with_open_housing_attr": open_docs,
                     "attribute_conflict": bool(open_docs) and len(open_docs) < len(with_attr)},
                    {"root": tree["group_id"], "required": sorted(direct), "household_or_groups": [g["group_id"] for g in hh_or]},
                    "주택 소유자를 무조건 부적격으로 판정 (원문에 무주택 요건이 없으면 잘못된 탈락)",
                    "무주택 필수 여부는 원문 확인 전 판정에 쓰지 말 것. 가구유형 OR의 '해당사항없음(JA0410)' 누락도 함께 확인")

        # S6 취업 조건 누락 (NOT 그룹의 EMPLOYMENT 제외 조건은 요건으로 세지 않음)
        employment_docs = {r["document_id"]: [v for v in as_list(r["attributes"].get("employment_status")) if not is_open_value(v)]
                           for r in records}
        specific_employment = {doc_id: values for doc_id, values in employment_docs.items() if values}
        open_employment = [r["document_id"] for r in records
                           if has_attr(r, "employment_status") and r["document_id"] not in specific_employment]
        if specific_employment and not any(c["type"] == "EMPLOYMENT" for c in positive.values()):
            values = sorted({str(v) for vs in specific_employment.values() for v in vs})
            add(policy_id, "S6_EMPLOYMENT_MISSING", "medium", "suspected" if open_employment else "mismatch",
                {"eligibility_attributes.employment_status_by_document": attr_by_document(records, "employment_status"),
                 "documents_with_specific_employment_attr": sorted(specific_employment),
                 "attribute_conflict": bool(open_employment), "text": evidence_by["employment"][:1]},
                graph_summary, f"취업 상태({','.join(values)}) 요건이 그래프에 없어 해당하지 않는 사용자도 통과",
                "취업 상태는 추가 질문으로 확인. 조건 생성 누락은 데이터 담당 공유")

        # S7 연령: 그래프 신청자 AGE 범위 vs 원문 신청자 연령 범위 (자녀 문맥 줄 제외)
        applicant_ranges, child_ranges = set(), set()
        for _source, _start, line in iter_lines(sources):
            if DATA_ERROR_LINE.search(line):
                continue
            target = child_ranges if CHILD_CONTEXT.search(line) else applicant_ranges
            target.update((int(a), int(b)) for a, b in AGE_RANGE.findall(line))
        ages = [c for c in positive.values() if c["type"] == "AGE"]
        for c in ages:
            parsed = parse_age_value(c.get("value"))
            if parsed is None:
                age_values_unparsed.append({"policy_id": policy_id, "condition_id": c["condition_id"], "value": str(c.get("value"))[:40]})
                continue
            low, high = parsed
            if applicant_ranges and parsed not in applicant_ranges and not any(lo == low for lo, _hi in applicant_ranges):
                add(policy_id, "S7_AGE_RANGE_MISMATCH", "high", "mismatch",
                    {"text_age_ranges": sorted(applicant_ranges), "child_age_ranges_excluded": sorted(child_ranges),
                     "text": evidence_by["age"][:1]},
                    {"condition_id": c["condition_id"], "value": c["value"]},
                    f"그래프 연령 {c['value']}세와 원문 연령 범위가 달라 나이 판정이 어긋남", "원문 연령으로 데이터 담당 확인")
            elif not evidence_by["age"] and high >= 120 and low > 0:      # 0~120은 사실상 제한 없음 → 위험 아님
                add(policy_id, "S7_AGE_GRAPH_ONLY", "medium", "suspected",
                    {"text_age": [], "child_age_lines": child_age_lines[:1]}, {"condition_id": c["condition_id"], "value": c["value"]},
                    f"원문에 신청자 연령 언급이 없는데 그래프는 {low}세 이상만 허용 → {low}세 미만 사용자를 잘못 탈락시킬 수 있음",
                    "연령 하한의 원천(JA0110/JA0111) 확인 전 자동 판정 보류")

        # S8 제외 조항이 그래프에 없음
        if evidence_by["exclusion"] and not any(g.get("logic") == "NOT" for g in groups):
            add(policy_id, "S8_EXCLUSION_NOT_MODELED", "medium", "not_modeled",
                {"text": evidence_by["exclusion"][:1]}, graph_summary,
                "원문의 제외·참여 제한 대상(중복수혜·부정수급 등)이 그래프에 없어 제외 대상자도 적격으로 안내",
                "제외 조항은 원문 근거로 안내하고 해당 여부를 추가 질문")

        # S9 지역 단위: 기관이 시·군·구인데 지역 노드는 시·도/전국
        organization = str(policy.get("organization") or policy.get("agency") or "")
        municipal = re.findall(r"([가-힣]+(?:시|군|구))(?=\s|$|/|\))", organization)
        municipal = [m for m in municipal if m not in region_names and not m.endswith(("특별시", "광역시", "특별자치시"))]
        graph_regions = [r.get("name") for r in context["regions"]]
        if municipal and all(r in region_names for r in graph_regions):
            add(policy_id, "S9_REGION_GRANULARITY", "high", "not_modeled",
                {"organization": organization[:80], "text": evidence_by["residence"][:1]},
                {"regions": graph_regions},
                f"{municipal[0]} 사업인데 지역이 {graph_regions}로만 표현 → 같은 시·도의 다른 시·군 거주자도 적격으로 안내",
                "지역 판정은 시·군·구 단위 원문 근거로. 그래프 Region만으로 거주 요건을 판정하지 말 것")

        # S10 혼인·자녀: 측면별로 그래프 관련 조건 존재 여부를 본다
        child_related = sorted(cid for cid in conditions if cid.startswith("COND_CHILD_") or cid in CHILD_RELATED_IDS)
        marriage_related = sorted(cid for cid, c in conditions.items()
                                  if MARRIAGE_CONDITION.search(f"{c.get('name') or ''} {c.get('value') or ''}"))
        related_by = {"marriage": marriage_related, "children": child_related}
        assessments = {name: assess_s10_dimension(name, sources, positive, related_by[name]) for name in S10_DIMENSIONS}
        by_class = {kind: [name for name in S10_DIMENSIONS if assessments[name]["classification"] == kind]
                    for kind in ("unmodeled", "incomplete", "review")}
        # root UNKNOWN은 UNKNOWN 경고가 전체 보류하므로 S10을 따로 만들지 않는다 (기존과 같음)
        if any(by_class.values()) and root_logic != "UNKNOWN":
            match = ("not_modeled" if by_class["unmodeled"] else "suspected" if by_class["incomplete"] else "unknown")
            add(policy_id, "S10_MARRIAGE_CHILD_NOT_MODELED", "low", match,
                {"marriage": evidence_by["marriage"][:1], "children": evidence_by["children"][:1],
                 "unmodeled_dimensions": by_class["unmodeled"], "incomplete_dimensions": by_class["incomplete"],
                 "review_dimensions": by_class["review"], "dimension_assessment": assessments,
                 "graph_related_conditions": related_by,
                 "eligibility_attributes.family_status_by_document": attr_by_document(records, "family_status"),
                 "note": "자동 의심 후보·검토 대상. 확정된 정책 오류 아님, 공식 원문 미검증"},
                graph_summary, "혼인 기간·자녀 수·자녀 연령 예외 등 요건을 그래프로 온전히 판정할 수 없음",
                "해당 측면은 추가 질문·원문으로 확인 (관련 조건이 있어도 충분성 미확인)")

        # 수집 오류 표기
        if DATA_ERROR_LINE.search(text):
            add(policy_id, "S11_SOURCE_AGE_ERROR_MARK", "low", "unknown",
                {"note": "원문에 '대상연령: 미상 (원문 0~0세 표기 오류…)' 표기"},
                {"age_conditions": [c.get("value") for c in ages]},
                "연령을 0~0세로 해석하면 전원 부적격", "연령은 판정 보류·공고 확인")

    # 조건이 없는 NOT 그룹: 제외 대상이 설명 문장에만 있고 평가할 조건이 없다
    for group in not_groups:
        if not group["conditions"]:
            add(group["policy_id"], "S12_EMPTY_NOT_GROUP", "medium", "not_modeled",
                {"group_description": (group["description"] or "")[:150]}, {"group_id": group["group_id"], "conditions": []},
                "빈 NOT 그룹을 판정기가 '제외 조건 없음'으로 처리하면 공무원·만 75세 이상 등 제외 대상도 적격으로 안내",
                "제외 대상은 설명·원문 근거로 안내하고 해당 여부를 추가 질문")
    # 의심 정책인데 조건 관계가 verified로 표기된 경우 (검토 상태 과대 표기)
    suspect_ids = {f["policy_id"] for f in findings if f["check"] in ("S2_INCOME_ALL_BANDS_OR", "S4_HOMELESS_REQUIRED_PROMOTED",
                                                                      "S7_AGE_RANGE_MISMATCH", "S6_EMPLOYMENT_MISSING")}
    verified_suspects = sorted(pid for pid in suspect_ids if any(
        (c.get("relationship") or {}).get("review_status") == "verified"
        for tree in contexts[pid]["eligibility"]["condition_tree"] for g, _n in walk(tree) for c in g.get("conditions", [])))
    summary = Counter((f["check"], f["severity"]) for f in findings)
    report = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "disclaimer": ("모든 findings는 자동 점검이 만든 '의심 후보'이며 확정된 정책 오류가 아니다. 근거 줄은 키워드·정규식 후보라 "
                       "오탐이 있을 수 있다. 공식 원문(정부 공고)과는 대조하지 않았다(C 미검증). "
                       "속성 부재(attribute_absent)는 '제한 없음'을 뜻하지 않는다. 인계 데이터·DB는 수정하지 않았다(읽기 전용)."),
        "official_source_checked": False,
        "dataset_version": dataset_version,
        "manifest_sha256": manifest_sha,
        "policy_source": "release neo4j/nodes_policy.jsonl",
        "eligibility_attributes_source": "hd.load_payload_metadata() — document_id 단위, 합치지 않음",
        "guards": {"expected_manifest_sha256": EXPECTED_MANIFEST_SHA256, "policies": len(policies), "documents": len(documents),
                   "neo4j_uri": EXPECTED_NEO4J_URI, "expected_totals": EXPECTED_TOTALS, "policy_id_set_match": True,
                   "note": "건수·Policy ID 집합 일치만 확인했다. DB 내용 전체 일치를 증명하지 않는다."},
        "rule_changes": RULE_CHANGES,
        "s10_rule": S10_RULE,
        "s10_dimension_counts": dict(sorted(Counter(
            f"{kind}:{name}" for f in findings if f["check"] == "S10_MARRIAGE_CHILD_NOT_MODELED"
            for kind in ("unmodeled", "incomplete", "review") for name in f["evidence"][f"{kind}_dimensions"]).items())),
        "evidence_levels": {"not_modeled": "그래프에 해당 조건 구조가 없음",
                            "mismatch": "원문 명시 표현 또는 인계 속성과 그래프가 다름",
                            "internal_inconsistency": "원문엔 없고 인계 속성과 그래프가 서로 다름",
                            "suspected": "패턴상 의심 (근거 부족 또는 문서 간 속성 불일치)", "unknown": "판정 불가"},
        "levels": {"A_structure": {"counts_before": counts_before, "counts_after": counts_after,
                                    "note": "총 노드·관계 건수와 Policy ID 집합만 확인 (내용 동일성은 미확인)"},
                   "B_handoff_semantic": "아래 findings", "C_official_source": "unverified (공식 사이트 미조회)"},
        "policies": len(policies), "condition_relationship_review_status": dict(review_status),
        "suspect_policies_with_verified_conditions": verified_suspects,
        "age_values_unparsed": age_values_unparsed,
        "check_counts": dict(sorted(Counter(f["check"] for f in findings).items())),
        "match_by_check": {check: dict(Counter(f["handoff_semantic_match"] for f in findings if f["check"] == check))
                           for check in sorted({f["check"] for f in findings})},
        "not_groups": not_groups, "region_nodes": sorted(region_names),
        "summary": {f"{check}|{severity}": count for (check, severity), count in sorted(summary.items())},
        "policies_with_findings": len({f["policy_id"] for f in findings}),
        "findings": findings,
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("dataset_version", "manifest_sha256", "policies", "check_counts",
                                              "suspect_policies_with_verified_conditions", "summary", "match_by_check",
                                              "policies_with_findings")}, ensure_ascii=False, indent=2))
    print("counts before/after:", counts_before, counts_after)
    print("age_values_unparsed:", len(age_values_unparsed))
    print("written:", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
