"""
Neo4j 자격 조건 의미 정합성 점검 (읽기 전용) — 2026-11-06

세 수준을 구분한다.
  A. 구조 정합성   : ID·노드·관계·참조 (기존 load_graph 검증·통합 테스트 재사용, 여기서는 건수만 재확인)
  B. 인계 의미 정합성: 조건 그래프가 인계 원문(data/processed/documents.jsonl 텍스트, policies.jsonl의
                     eligibility·eligibility_attributes)의 의미를 맞게 표현하는가
  C. 공식 원문 정합성: 실제 정부 공고와 같은가 → 로컬에 원본(data/raw)이 없고 공식 사이트를 조회하지 않았으므로 전부 "unverified"

규칙
- Neo4j·Qdrant에 쓰지 않는다 (읽기 전용 세션 open_neo4j_runner + get_policy_graph_context).
- 원문에 명확한 근거가 없으면 조건을 추론하지 않는다. 키워드로 찾은 줄은 "근거 후보"이며 판정은 사람이 확인해야 한다.
- UNKNOWN은 적격·부적격 어느 쪽으로도 확정하지 않는다.

실행 (프로젝트 루트, .venv):
    .venv\\Scripts\\python.exe docs/development/experiments/neo4j_semantic_20261106/neo4j_semantic_check.py
결과: 같은 폴더 neo4j_semantic_check.json (원문은 근거 줄 150자 이내만)
"""

import ast
import json
import re
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database import handoff_data as hd  # noqa: E402
from database import hybrid_search as hs  # noqa: E402

OUT = Path(__file__).resolve().parent / "neo4j_semantic_check.json"
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
DATA_ERROR_LINE = re.compile(r"원문 0~0세 표기 오류")
AGE_RANGE = re.compile(r"(\d{1,2})\s?세?\s?[~∼～-]\s?(?:만\s?)?(\d{1,2})\s?세")
OPEN_TERMS = ("제한없음", "해당없음", "", "무관")
EMPTY_EXCLUSION = re.compile(r"(?:제한\s?대상|제외\s?대상)\s*[:：]?\s*[-–—]?\s*$")   # "참여제한대상: -" 처럼 내용 없음


def is_open_value(value) -> bool:
    """속성값이 '제한 없음/미확인' 계열인가 (예: '제한없음/미확인', '해당없음')."""
    text = str(value)
    return text in OPEN_TERMS or "제한없음" in text or "미확인" in text or "해당없음" in text


def lines_with(text: str, pattern: re.Pattern, limit: int = 2) -> list[dict]:
    found = []
    for match in re.finditer(r"[^\r\n]+", text):
        line = match.group(0).strip()
        if pattern.search(line) and not DATA_ERROR_LINE.search(line) and not EMPTY_EXCLUSION.search(line):
            found.append({"char_start": match.start(), "line": line[:150]})
            if len(found) >= limit:
                break
    return found


def walk(tree: dict) -> list[dict]:
    groups = [tree]
    for child in tree.get("groups", []):
        groups += walk(child)
    return groups


def parse_attrs(policy: dict) -> dict:
    raw = policy.get("eligibility_attributes")
    try:
        return ast.literal_eval(raw) if isinstance(raw, str) and raw.strip() else (raw or {})
    except (ValueError, SyntaxError):
        return {"_parse_error": str(raw)[:80]}


def main() -> int:
    policies = {p["policy_id"]: p for p in (json.loads(l) for l in open(hd.PROJECT_ROOT / "data/processed/policies.jsonl", encoding="utf-8"))}
    documents = hd.load_documents(verify_hash=True)
    docs_by_policy: dict[str, list[dict]] = {}
    for document in documents:
        docs_by_policy.setdefault(document["policy_id"], []).append(document)
    with hs.open_neo4j_runner() as runner:
        counts_before = runner.read_rows(hs.lg.QUERY_TOTAL_COUNTS)[0]
        contexts = {}
        ids = sorted(policies)
        for start in range(0, len(ids), 50):
            contexts.update(hs.get_policy_graph_context(ids[start:start + 50], runner=runner)["policies"])
        region_names = {row["name"] for row in runner.read_rows("MATCH (r:Region) RETURN r.name AS name")}
        counts_after = runner.read_rows(hs.lg.QUERY_TOTAL_COUNTS)[0]

    findings: list[dict] = []
    review_status = Counter()
    not_groups = []

    def add(policy_id, check, severity, match, evidence, graph, wrong, action):
        findings.append({"policy_id": policy_id, "policy_name": policies[policy_id]["policy_name"][:60],
                         "document_ids": [d["document_id"] for d in docs_by_policy.get(policy_id, [])],
                         "check": check, "severity": severity, "handoff_semantic_match": match,
                         "evidence": evidence, "graph": graph, "wrong_recommendation_example": wrong,
                         "recommended_action": action,
                         "levels": {"A_structure": "pass", "B_handoff_semantic": match, "C_official_source": "unverified"}})

    for policy_id, policy in sorted(policies.items()):
        context = contexts[policy_id]
        eligibility = context["eligibility"]
        trees = eligibility["condition_tree"]
        groups = [g for tree in trees for g in walk(tree)]
        conditions = {c["condition_id"]: c for g in groups for c in g.get("conditions", [])}
        for g in groups:
            for c in g.get("conditions", []):
                review_status[(c.get("relationship") or {}).get("review_status")] += 1
            if g.get("logic") == "NOT":
                not_groups.append({"policy_id": policy_id, "group_id": g["group_id"],
                                   "conditions": [c["name"] for c in g.get("conditions", [])], "description": g.get("description")})
        text = "\n".join(d["text"] for d in docs_by_policy.get(policy_id, [])) + "\n" + str(policy.get("eligibility") or "")
        evidence_by = {name: lines_with(text, pattern) for name, pattern in PATTERNS.items()}
        attrs = parse_attrs(policy)
        root_logic = trees[0]["logic"] if trees else None
        graph_summary = {"root_logic": root_logic, "groups": [(g["group_id"], g.get("logic")) for g in groups],
                         "conditions": sorted(conditions)}

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
                income_attr = attrs.get("income_level")
                specific = [v for v in (income_attr or []) if "%" in str(v) and "무관" not in str(v)]
                # 원문에 소득 기준이 있으면 원문과 불일치, 원문엔 없고 인계 속성만 상한을 말하면 인계 데이터 내부 불일치
                match = ("mismatch" if evidence_by["income"] else
                         "internal_inconsistency" if specific else "suspected")
                add(policy_id, "S2_INCOME_ALL_BANDS_OR", "high" if match != "suspected" else "medium", match,
                    {"eligibility_attributes.income_level": income_attr, "text": evidence_by["income"][:1]},
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
                housing_attr = attrs.get("housing_status") or []
                # 원문에 무주택 언급이 없고 인계 속성도 '해당없음'이면 불일치, 원문에 무주택이 있으면 필수 여부만 의심
                match = "suspected" if evidence_by["housing"] else (
                    "mismatch" if all(is_open_value(v) for v in housing_attr) else "suspected")
                add(policy_id, "S4_HOMELESS_REQUIRED_PROMOTED", "high", match,
                    {"text_무주택": evidence_by["housing"][:1], "eligibility_attributes.housing_status": attrs.get("housing_status")},
                    {"root": tree["group_id"], "required": sorted(direct), "household_or_groups": [g["group_id"] for g in hh_or]},
                    "주택 소유자를 무조건 부적격으로 판정 (원문에 무주택 요건이 없으면 잘못된 탈락)",
                    "무주택 필수 여부는 원문 확인 전 판정에 쓰지 말 것. 가구유형 OR의 '해당사항없음(JA0410)' 누락도 함께 확인")

        # S6 취업 조건 누락
        employment_attr = [v for v in (attrs.get("employment_status") or []) if not is_open_value(v)]
        if employment_attr and not any(c["type"] == "EMPLOYMENT" for c in conditions.values()):
            add(policy_id, "S6_EMPLOYMENT_MISSING", "medium", "mismatch",
                {"eligibility_attributes.employment_status": employment_attr, "text": evidence_by["employment"][:1]},
                graph_summary, f"취업 상태({','.join(map(str, employment_attr))}) 요건이 그래프에 없어 해당하지 않는 사용자도 통과",
                "취업 상태는 추가 질문으로 확인. 조건 생성 누락은 데이터 담당 공유")

        # S7 연령: 그래프 범위 vs 원문 범위
        ages = [c for c in conditions.values() if c["type"] == "AGE"]
        text_ranges = {(int(a), int(b)) for a, b in AGE_RANGE.findall(text)}
        for c in ages:
            low, high = (int(x) for x in c["value"].split("~"))
            if text_ranges and (low, high) not in text_ranges and not any(lo == low for lo, _hi in text_ranges):
                add(policy_id, "S7_AGE_RANGE_MISMATCH", "high", "mismatch",
                    {"text_age_ranges": sorted(text_ranges), "text": evidence_by["age"][:1]},
                    {"condition_id": c["condition_id"], "value": c["value"]},
                    f"그래프 연령 {c['value']}세와 원문 연령 범위가 달라 나이 판정이 어긋남", "원문 연령으로 데이터 담당 확인")
            elif not evidence_by["age"] and high >= 120 and low > 0:      # 0~120은 사실상 제한 없음 → 위험 아님
                add(policy_id, "S7_AGE_GRAPH_ONLY", "medium", "suspected",
                    {"text_age": []}, {"condition_id": c["condition_id"], "value": c["value"]},
                    f"원문에 연령 언급이 없는데 그래프는 {low}세 이상만 허용 → {low}세 미만 사용자를 잘못 탈락시킬 수 있음",
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

        # S10 혼인·자녀 조건은 조건 타입이 없음
        if (evidence_by["marriage"] or evidence_by["children"]) and root_logic != "UNKNOWN":
            add(policy_id, "S10_MARRIAGE_CHILD_NOT_MODELED", "low", "not_modeled",
                {"marriage": evidence_by["marriage"][:1], "children": evidence_by["children"][:1]}, graph_summary,
                "혼인 기간·자녀 수 요건을 그래프로 판정할 수 없음", "추가 질문으로 확인 (EligibilityCondition에 해당 타입 없음)")

        # 수집 오류 표기
        if DATA_ERROR_LINE.search(text):
            add(policy_id, "S11_SOURCE_AGE_ERROR_MARK", "low", "unknown",
                {"note": "원문에 '대상연령: 미상 (원문 0~0세 표기 오류…)' 표기"},
                {"age_conditions": [c["value"] for c in ages]},
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
        for tree in contexts[pid]["eligibility"]["condition_tree"] for g in walk(tree) for c in g.get("conditions", [])))
    summary = Counter((f["check"], f["severity"]) for f in findings)
    report = {
        "created_at": __import__("datetime").datetime.now().astimezone().isoformat(timespec="seconds"),
        "disclaimer": ("findings는 확정된 정책 오류가 아니라 '문제 또는 판정 불가 후보'다. 근거 줄은 키워드·정규식으로 찾은 "
                       "후보라 오탐이 있을 수 있다(예: 자녀 나이 '6세 이하'가 신청자 연령으로 잡힘). 공식 원문(정부 공고)과는 "
                       "대조하지 않았다(C 미검증). 인계 데이터·DB는 수정하지 않았다."),
        "evidence_levels": {"not_modeled": "그래프에 해당 조건 구조가 없음",
                            "mismatch": "원문 명시 표현 또는 인계 속성과 그래프가 다름",
                            "internal_inconsistency": "원문엔 없고 인계 속성과 그래프가 서로 다름",
                            "suspected": "패턴상 의심 (근거 부족)", "unknown": "판정 불가"},
        "manifest_sha256": __import__("database.full_index", fromlist=["manifest_sha256"]).manifest_sha256(),
        "levels": {"A_structure": {"counts_before": counts_before, "counts_after": counts_after,
                                    "note": "ID·관계·속성 원본 일치는 기존 검증·통합 테스트(test_neo4j_smoke) 통과로 확인"},
                   "B_handoff_semantic": "아래 findings", "C_official_source": "unverified (data/raw 없음, 공식 사이트 미조회)"},
        "policies": len(policies), "condition_relationship_review_status": dict(review_status),
        "suspect_policies_with_verified_conditions": verified_suspects,
        "match_by_check": {check: dict(Counter(f["handoff_semantic_match"] for f in findings if f["check"] == check))
                           for check in sorted({f["check"] for f in findings})},
        "not_groups": not_groups, "region_nodes": sorted(region_names),
        "summary": {f"{check}|{severity}": count for (check, severity), count in sorted(summary.items())},
        "policies_with_findings": len({f["policy_id"] for f in findings}),
        "findings": findings,
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("policies", "condition_relationship_review_status",
                                              "suspect_policies_with_verified_conditions", "summary", "match_by_check",
                                              "policies_with_findings")}, ensure_ascii=False, indent=2))
    print("counts before/after:", counts_before, counts_after)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
