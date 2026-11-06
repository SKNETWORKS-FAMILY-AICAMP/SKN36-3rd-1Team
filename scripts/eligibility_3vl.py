"""Issue #21 후보용 참조 3값 논리 평가기 (읽기 전용, DB/네트워크 없음).

docs/data/db_schema.md 3값 논리 그대로:
  AND: 하나라도 FALSE면 FALSE, 모두 TRUE면 TRUE, 그 외 UNKNOWN
  OR : 하나라도 TRUE면 TRUE, 모두 FALSE면 FALSE, 그 외 UNKNOWN
  NOT: TRUE↔FALSE, UNKNOWN 유지
  UNKNOWN 그룹: 자식과 무관하게 UNKNOWN

원자 조건은 프로필에 값이 없거나 연산자가 UNKNOWN이면 UNKNOWN이다.
결과 FALSE만 '확인된 조건 불충족'으로 쓸 수 있고, TRUE/UNKNOWN은 자동 적격 판정이 아니다.

사용 예:
  python scripts/eligibility_3vl.py --candidate data/candidates/issue21-v1.4.0-20261107-final2 \
      --age 29 --region 충청남도 --employment 미취업자
"""
import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

TRUE, FALSE, UNKNOWN = "TRUE", "FALSE", "UNKNOWN"
# EligibilityCondition.type → 프로필 키
PROFILE_KEYS = {
    "EMPLOYMENT": "employment", "EDUCATION": "education", "SPECIAL_TARGET": "special",
    "HOUSING_HOUSEHOLD": "household", "INCOME": "income_band",
}
NATIONAL = "전국"


def slug(label):
    return re.sub(r"[^0-9A-Za-z가-힣]+", "_", str(label))


def rows(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


class Candidate:
    """후보 디렉터리의 neo4j/*.jsonl을 메모리에 올린다."""

    def __init__(self, directory):
        base = Path(directory) / "neo4j"
        self.policies = {p["policy_id"]: p for p in rows(base / "nodes_policy.jsonl")}
        self.groups = {g["group_id"]: g for g in rows(base / "nodes_condition_group.jsonl")}
        self.conditions = {c["condition_id"]: c for c in rows(base / "nodes_eligibility_condition.jsonl")}
        self.regions = {r["region_id"]: r for r in rows(base / "nodes_region.jsonl")}
        self.children = defaultdict(list)
        self.root = {}
        self.available_in = defaultdict(list)
        for e in rows(base / "relationships.jsonl"):
            if e["relation"] in ("CONTAINS_GROUP", "CONTAINS_CONDITION"):
                self.children[e["source_id"]].append(e)
            elif e["relation"] == "HAS_ROOT_CONDITION_GROUP":
                self.root[e["source_id"]] = e["target_id"]
            elif e["relation"] == "AVAILABLE_IN":
                self.available_in[e["source_id"]].append(self.regions.get(e["target_id"], {}).get("name"))


def evaluate_atom(condition, profile):
    """원자 조건 하나. 판단 근거가 없으면 UNKNOWN."""
    ctype, op, value = condition.get("type"), condition.get("operator"), str(condition.get("value"))
    if op == "UNKNOWN":
        return UNKNOWN
    if ctype == "AGE" and op == "BETWEEN":
        age = profile.get("age")
        if age is None:
            return UNKNOWN
        low, high = (int(v) for v in value.split("~"))
        return TRUE if low <= age <= high else FALSE
    key = PROFILE_KEYS.get(ctype)
    if key is None or profile.get(key) is None:
        return UNKNOWN
    return TRUE if value in {slug(v) for v in profile[key]} else FALSE


def evaluate_group(candidate, group_id, profile, trace=None):
    logic = candidate.groups[group_id]["logic"]
    if logic == "UNKNOWN":
        if trace is not None:
            trace.append((group_id, UNKNOWN))
        return UNKNOWN
    values = []
    for e in candidate.children[group_id]:
        if e["relation"] == "CONTAINS_GROUP":
            values.append(evaluate_group(candidate, e["target_id"], profile, trace))
        else:
            value = evaluate_atom(candidate.conditions[e["target_id"]], profile)
            if trace is not None:
                trace.append((e["target_id"], value))
            values.append(value)
    if logic == "AND":
        result = FALSE if FALSE in values else (TRUE if values and all(v == TRUE for v in values) else UNKNOWN)
    elif logic == "OR":
        result = TRUE if TRUE in values else (FALSE if values and all(v == FALSE for v in values) else UNKNOWN)
    elif logic == "NOT":
        result = {TRUE: FALSE, FALSE: TRUE}.get(values[0], UNKNOWN) if len(values) == 1 else UNKNOWN
    else:
        raise ValueError(f"알 수 없는 logic: {logic}")
    return result


def region_match(candidate, policy_id, region):
    """지역 검색: 시도 관계로 찾고, 시군구 한정 정책은 시도 일치만으로 확정하지 않는다."""
    policy = candidate.policies[policy_id]
    names = candidate.available_in[policy_id]
    if region is None:
        return UNKNOWN
    if NATIONAL in names or region in names:
        return UNKNOWN if policy.get("region_detail") else TRUE
    return FALSE


def evaluate_policy(candidate, policy_id, profile):
    trace = []
    conditions = evaluate_group(candidate, candidate.root[policy_id], profile, trace)
    region = region_match(candidate, policy_id, profile.get("region"))
    overall = FALSE if FALSE in (conditions, region) else (TRUE if conditions == region == TRUE else UNKNOWN)
    policy = candidate.policies[policy_id]
    return {"policy_id": policy_id, "policy_name": policy.get("policy_name"), "conditions": conditions,
            "region": region, "region_detail": policy.get("region_detail"), "overall": overall,
            "auto_eligibility": False if policy.get("eligibility_review_status") == "needs_review" else overall == TRUE,
            "failed_conditions": [cid for cid, value in trace if value == FALSE]}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--age", type=int)
    parser.add_argument("--region")
    for key in ("employment", "education", "special", "household", "income_band"):
        parser.add_argument(f"--{key.replace('_', '-')}", action="append", dest=key)
    parser.add_argument("--show", type=int, default=5)
    args = parser.parse_args()
    profile = {k: v for k, v in vars(args).items() if k not in ("candidate", "show")}
    candidate = Candidate(args.candidate)
    results = [evaluate_policy(candidate, pid, profile) for pid in sorted(candidate.policies)]
    print(json.dumps({"profile": profile, "overall": Counter(r["overall"] for r in results),
                      "conditions": Counter(r["conditions"] for r in results),
                      "region": Counter(r["region"] for r in results),
                      "auto_eligibility_true": sum(r["auto_eligibility"] for r in results)}, ensure_ascii=False))
    for label in (FALSE, UNKNOWN):
        for r in [r for r in results if r["overall"] == label][:args.show]:
            print(label, json.dumps(r, ensure_ascii=False))


if __name__ == "__main__":
    main()
