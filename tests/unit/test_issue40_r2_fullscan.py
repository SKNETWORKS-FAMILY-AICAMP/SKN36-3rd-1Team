"""#40: scan every r2 policy carrying UNKNOWN, without requiring live DBs."""

from collections import defaultdict
import json
from pathlib import Path

from database import data_quality_flags
from database.hybrid_search import _build_condition_tree, assess_policy_data_quality
from database.unknown_group_scope import scoped_income_quality_attributes

DATA = Path(__file__).resolve().parents[2] / "data/releases/v1.4.0-candidate/neo4j"
EXPECTED = {
    "POL_GOV_001", "POL_GOV_007", "POL_GOV_035",
    "POL_GOV_039", "POL_GOV_042", "POL_GOV_047",
}


def read_jsonl(name):
    with (DATA / name).open(encoding="utf-8") as file:
        return [json.loads(line) for line in file if line.strip()]


def test_r2_all_189_unknown_policies_only_narrow_six():
    groups = {g["group_id"]: g for g in read_jsonl("nodes_condition_group.jsonl")}
    conditions = {c["condition_id"]: c for c in read_jsonl("nodes_eligibility_condition.jsonl")}
    policies = {p["policy_id"]: p for p in read_jsonl("nodes_policy.jsonl")}
    relations = defaultdict(list)
    for rel in read_jsonl("relationships.jsonl"):
        relations[rel["source_id"]].append(rel)
    flags = data_quality_flags.load_flags().get("flags_by_policy", {})

    unknown_policies = {
        g["policy_id"] for g in groups.values()
        if str(g.get("logic") or "").upper() == "UNKNOWN"
    }
    # 복지로 2026 추가 정책 9건은 루트 UNKNOWN이라 189 → 198
    assert len(unknown_policies) == 198, (
        f"UNKNOWN policy population changed: expected 198, got {len(unknown_policies)}"
    )

    allowed = set()
    for pid in sorted(unknown_policies):
        rows = {}
        for gid, group in groups.items():
            if group["policy_id"] != pid:
                continue
            edges = relations[gid]
            rows[gid] = {
                "group": group,
                "child_groups": [
                    {"group_id": edge["target_id"], "relationship": edge}
                    for edge in edges if edge["relation"] == "CONTAINS_GROUP"
                ],
                "conditions": [
                    {"condition": conditions[edge["target_id"]], "relationship": edge}
                    for edge in edges if edge["relation"] == "CONTAINS_CONDITION"
                ],
            }
        roots = [
            edge for edge in relations[pid]
            if edge["relation"] == "HAS_ROOT_CONDITION_GROUP"
        ]
        if len(roots) != 1:
            # Missing or ambiguous roots must NEVER gain scoped narrowing.
            eligibility = {
                "condition_tree": [], "unknown_logic_groups": [],
                "structure_issues": ["invalid_root_count"],
            }
        else:
            issues = []
            root = _build_condition_tree(
                pid, roots[0]["target_id"], rows, roots[0], issues,
            )
            unknown_ids = []

            def collect(group):
                if group.get("logic") == "UNKNOWN":
                    unknown_ids.append(group.get("group_id"))
                for child in group.get("groups", []):
                    collect(child)

            collect(root)
            eligibility = {
                "condition_tree": [root],
                "unknown_logic_groups": sorted(unknown_ids),
                "structure_issues": issues,
            }

        quality = assess_policy_data_quality(
            {"policy": policies[pid], "eligibility": eligibility},
            static_flags=flags.get(pid, []),
        )
        if scoped_income_quality_attributes(eligibility, quality) is not None:
            allowed.add(pid)

    assert allowed == EXPECTED, (
        f"extra narrowed: {sorted(allowed - EXPECTED)}; "
        f"expected but missing: {sorted(EXPECTED - allowed)}"
    )
