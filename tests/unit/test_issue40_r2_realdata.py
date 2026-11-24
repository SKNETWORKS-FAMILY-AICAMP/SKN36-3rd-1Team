
"""Issue #40: r2 실제 JSONL 기반 UNKNOWN/AND 회귀 테스트."""

import json
import re
from collections import defaultdict
from pathlib import Path

import pytest

from backend.eligibility.checker import check_eligibility
from database import data_quality_flags
from database.hybrid_search import (
    _build_condition_tree,
    assess_policy_data_quality,
)
from database.unknown_group_scope import (
    income_only_unknown_under_and,
    scoped_income_quality_attributes,
)

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data/releases/v1.4.0-candidate/neo4j"

UNKNOWN_POLICIES = (
    "POL_GOV_001",
    "POL_GOV_007",
    "POL_GOV_035",
    "POL_GOV_039",
    "POL_GOV_042",
    "POL_GOV_047",
)


def read_jsonl(name):
    path = DATA / name
    with path.open(encoding="utf-8") as file:
        return [json.loads(line) for line in file if line.strip()]


@pytest.fixture(scope="module")
def r2():
    groups = {
        row["group_id"]: row
        for row in read_jsonl("nodes_condition_group.jsonl")
    }
    conditions = {
        row["condition_id"]: row
        for row in read_jsonl("nodes_eligibility_condition.jsonl")
    }
    policies = {
        row["policy_id"]: row
        for row in read_jsonl("nodes_policy.jsonl")
    }

    relations = defaultdict(list)
    for relation in read_jsonl("relationships.jsonl"):
        relations[relation["source_id"]].append(relation)

    flags = data_quality_flags.load_flags()

    return {
        "groups": groups,
        "conditions": conditions,
        "policies": policies,
        "relations": relations,
        "flags": flags.get("flags_by_policy", {}),
    }


def build_eligibility(policy_id, r2):
    """JSONL 관계에서 실제 Neo4j 조회 형태의 트리를 조립."""
    rows = {}

    for group_id, group in r2["groups"].items():
        if group["policy_id"] != policy_id:
            continue

        links = r2["relations"][group_id]

        rows[group_id] = {
            "group": group,
            "child_groups": [
                {
                    "group_id": link["target_id"],
                    "relationship": link,
                }
                for link in links
                if link["relation"] == "CONTAINS_GROUP"
            ],
            "conditions": [
                {
                    "condition": r2["conditions"][link["target_id"]],
                    "relationship": link,
                }
                for link in links
                if link["relation"] == "CONTAINS_CONDITION"
            ],
        }

    roots = [
        link
        for link in r2["relations"][policy_id]
        if link["relation"] == "HAS_ROOT_CONDITION_GROUP"
    ]
    assert len(roots) == 1, f"{policy_id}: 루트 그룹 확인 필요"

    issues = []
    root = _build_condition_tree(
        policy_id,
        roots[0]["target_id"],
        rows,
        roots[0],
        issues,
    )

    def unknown_ids(group):
        result = []
        if group["logic"] == "UNKNOWN":
            result.append(group["group_id"])
        for child in group.get("groups", []):
            result.extend(unknown_ids(child))
        return result

    return {
        "condition_tree": [root],
        "unknown_logic_groups": sorted(unknown_ids(root)),
        "structure_issues": issues,
    }


def make_policy(policy_id, eligibility, quality):
    return {
        "policy_id": policy_id,
        "graph_status": "FOUND",
        "graph": {"eligibility": eligibility},
        "data_quality": quality,
    }


def root_status(result):
    return next(
        item["status"]
        for item in reversed(result["conditions"])
        if item["field"] == "eligibility"
    )


@pytest.mark.parametrize("policy_id", UNKNOWN_POLICIES)
def test_r2_income_unknown_scoping(policy_id, r2):
    eligibility = build_eligibility(policy_id, r2)

    assert eligibility["structure_issues"] == []
    assert len(eligibility["unknown_logic_groups"]) == 1
    assert income_only_unknown_under_and(eligibility)

    context = {
        "policy": r2["policies"][policy_id],
        "eligibility": eligibility,
    }
    quality = assess_policy_data_quality(
        context,
        static_flags=r2["flags"].get(policy_id, []),
    )

    scope = scoped_income_quality_attributes(
        eligibility, quality
    )

    warnings = [
        f"{w['code']}:{w.get('affects', [])}"
        for w in quality["warnings"]
        if w.get("blocks_auto_eligibility")
    ]

    print(
        f"\n{policy_id}"
        f"\n  scoped={sorted(scope) if scope is not None else 'GLOBAL_HOLD'}"
        f"\n  warnings={warnings}"
    )

    # 정보가 부족한 프로필로 전체 자격 충족을 확정하지 않는다.
    result = check_eligibility(
        {"age": None},
        make_policy(policy_id, eligibility, quality),
    )
    assert root_status(result) == "needs_check"

    # 실제 루트에 AGE 조건이 있고, 다른 경고가 AGE를
    # 보류하지 않을 때만 확정 미충족을 검증한다.
    root = eligibility["condition_tree"][0]
    for condition in root["conditions"]:
        if condition.get("type") != "AGE":
            continue
        if condition.get("operator") != "BETWEEN":
            continue

        match = re.fullmatch(
            r"(\d+)~(\d+)",
            str(condition.get("value", "")),
        )
        if not match:
            continue

        low, high = map(int, match.groups())
        failing_age = (
            low - 1 if low > 0
            else high + 1 if high < 120
            else None
        )

        if (
            failing_age is not None
            and scope is not None
            and "age" not in scope
        ):
            failed = check_eligibility(
                {"age": failing_age},
                make_policy(policy_id, eligibility, quality),
            )
            assert root_status(failed) == "ineligible"
            print(f"  age={failing_age}: ineligible PASS")
        break


def test_r2_gov016_no_longer_has_unknown(r2):
    policy_id = "POL_GOV_016"
    eligibility = build_eligibility(policy_id, r2)

    assert eligibility["structure_issues"] == []
    assert eligibility["unknown_logic_groups"] == []
    assert not income_only_unknown_under_and(eligibility)

    policy = make_policy(policy_id, eligibility, {})

    # 실제 AGE 19~120 AND 구조의 미충족 보존
    result = check_eligibility({"age": 18}, policy)
    assert root_status(result) == "ineligible"

    # 나이 충족만으로 SPECIAL_TARGET 요건을 확정하지 않는다.
    result = check_eligibility({"age": 25}, policy)
    assert root_status(result) == "needs_check"
