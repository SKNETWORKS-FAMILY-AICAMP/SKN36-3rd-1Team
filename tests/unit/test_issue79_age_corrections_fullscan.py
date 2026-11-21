
"""Issue #79: v1.4.0-candidate applicant age correction full scan.

Verify recorded age corrections against actual Neo4j relationships
and the Checker's inclusive age boundaries.

No Neo4j, Qdrant, or LLM connection is required.
"""

import json
import re
from collections import defaultdict
from pathlib import Path

import pytest

from backend.eligibility import checker


ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
NEO4J = DATA / "releases/v1.4.0-candidate/neo4j"


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(filename):
    path = NEO4J / filename
    with path.open(encoding="utf-8") as stream:
        return [
            json.loads(line)
            for line in stream
            if line.strip()
        ]


corrections = load_json(
    DATA / "metadata/data_corrections_v1.4.0.json"
)

conditions = {
    row["condition_id"]: row
    for row in load_jsonl("nodes_eligibility_condition.jsonl")
}

groups = {
    row["group_id"]: row
    for row in load_jsonl("nodes_condition_group.jsonl")
}

relationships = load_jsonl("relationships.jsonl")

roots = {}
child_groups = defaultdict(set)
child_conditions = defaultdict(set)
required_conditions = set()

for rel in relationships:
    relation = rel["relation"]
    source = rel["source_id"]
    target = rel["target_id"]

    if relation == "HAS_ROOT_CONDITION_GROUP":
        roots[source] = target
    elif relation == "CONTAINS_GROUP":
        child_groups[source].add(target)
    elif relation == "CONTAINS_CONDITION":
        child_conditions[source].add(target)
    elif relation == "REQUIRES_CONDITION":
        required_conditions.add((source, target))


recorded_group_additions = {
    (row["group_id"], row["condition_id"])
    for row in corrections["group_condition_additions"]
}


def reachable_groups(policy_id):
    """Find all group IDs reachable from the policy's root."""
    assert policy_id in roots, (
        f"{policy_id}: root group missing"
    )

    visited = set()
    stack = [roots[policy_id]]

    while stack:
        group_id = stack.pop()

        if group_id in visited:
            continue

        assert group_id in groups, (
            f"{policy_id}: missing group {group_id}"
        )

        visited.add(group_id)
        stack.extend(child_groups[group_id])

    return visited


def applicant_age_additions():
    """Select only actual AGE nodes from correction records."""
    return [
        row
        for row in corrections["requires_condition_additions"]
        if conditions.get(
            row["condition_id"], {}
        ).get("type") == "AGE"
    ]


age_records = applicant_age_additions()


def test_issue79_applicant_age_correction_count():
    """Exactly 53 distinct applicant policies were corrected."""
    policy_ids = [
        row["policy_id"] for row in age_records
    ]

    assert len(age_records) == 53
    assert len(set(policy_ids)) == 53

    notes = corrections["age_condition_notes"]

    # Explicitly held cases must not enter automatic AGE additions.
    assert len(notes["held"]) == 6
    assert len(notes["no_applicant_age"]) == 1

    assert not (
        set(policy_ids) & set(notes["held"])
    )
    assert not (
        set(policy_ids) & set(notes["no_applicant_age"])
    )


@pytest.mark.parametrize(
    "record",
    age_records,
    ids=[row["policy_id"] for row in age_records],
)
def test_issue79_age_graph_and_boundaries(record):
    """Validate AGE relationship, graph reachability, and boundaries."""
    policy_id = record["policy_id"]
    condition_id = record["condition_id"]

    # 1. Correction must reference an existing AGE node.
    assert condition_id in conditions
    condition = conditions[condition_id]

    assert condition["type"] == "AGE"
    assert condition["operator"] == "BETWEEN"

    # 2. Actual REQUIRES_CONDITION relationship must exist.
    assert (policy_id, condition_id) in required_conditions

    # 3. AGE must also be reachable through the condition tree.
    policy_groups = reachable_groups(policy_id)

    attached_groups = {
        group_id
        for group_id in policy_groups
        if condition_id in child_conditions[group_id]
    }

    assert attached_groups, (
        f"{policy_id}: AGE node not reachable from root"
    )

    # The attachment must match the correction record.
    assert any(
        (group_id, condition_id) in recorded_group_additions
        for group_id in attached_groups
    ), f"{policy_id}: missing correction group attachment"

    # 4. Recorded condition ID independently specifies bounds.
    match = re.fullmatch(
        r"COND_AGE_(\d+)_(\d+)",
        condition_id,
    )
    assert match is not None, condition_id

    low, high = map(int, match.groups())

    assert condition["value"] == f"{low}~{high}"
    assert 0 <= low <= high <= 120

    # 5. Verify individual AGE condition boundaries.
    # Age -1 and 121 are invalid inputs, so require needs_check.
    cases = [
        (low - 1, checker.CHECK if low == 0 else checker.NO),
        (low, checker.OK),
        (high, checker.OK),
        (high + 1, checker.CHECK if high == 120 else checker.NO),
        (None, checker.CHECK),
    ]

    for age, expected in cases:
        actual = checker._condition(
            condition,
            {"age": age},
        )

        assert actual["field"] == "age"
        assert actual["status"] == expected, (
            f"{policy_id}: age={age}, "
            f"expected={expected}, "
            f"actual={actual['status']}"
        )


@pytest.mark.parametrize(
    "replacement",
    corrections["age_condition_replacements"],
    ids=[
        row["policy_id"]
        for row in corrections["age_condition_replacements"]
    ],
)
def test_issue79_age_replacements(replacement):
    """Check all five documented age condition replacements."""
    policy_id = replacement["policy_id"]
    old_id = replacement["before"]
    new_id = replacement["after"]

    policy_groups = reachable_groups(policy_id)

    reachable_conditions = set()
    for group_id in policy_groups:
        reachable_conditions.update(
            child_conditions[group_id]
        )

    assert new_id in reachable_conditions
    assert old_id not in reachable_conditions
    assert (policy_id, new_id) in required_conditions


def test_issue79_pending_review_remains_separate():
    """#90: Keep the Incheon age evidence pending until verified."""

    # TODO(#90):
    # 공식 사업안내서가 Document로 추가되고 근거 관계의
    # review_status가 해제되면 이 테스트도 함께 갱신한다.
    # 현재는 검수 대기 상태가 실수로 해제되지 않았는지 확인한다.

    policy_id = "POL_STG_ICN_001"
    condition_id = "COND_AGE_19_39"

    matches = [
        row
        for row in relationships
        if row["target_id"] == condition_id
        and row["source_id"] in {
            policy_id,
            "GRP_POL_STG_ICN_001_ROOT",
        }
    ]

    assert len(matches) == 2

    assert {
        row.get("review_status")
        for row in matches
    } == {"needs_review"}
