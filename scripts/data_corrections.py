# -*- coding: utf-8 -*-
"""
v1.4.0 후보 릴리스에 적용하는 원천 데이터 교정 (data/metadata/data_corrections_v1.4.0.json).

data/handoff(v1.3.2)는 고정본이라 수정하지 않고, 후보를 만들 때 원문과 다르다고 확인된 값만 교정한다.
노드·관계 유형과 검색 응답 구조는 바꾸지 않는다. 각 교정은 '수정 전 값'을 함께 선언하므로
입력 데이터가 예상과 다르면 조용히 넘어가지 않고 빌드를 중단한다.
"""
import json
from pathlib import Path

CORRECTIONS_PATH = Path(__file__).resolve().parent.parent / "data" / "metadata" / "data_corrections_v1.4.0.json"
HOUSEHOLD_CONDITION_PREFIX = "COND_HH_"
HOMELESS_CONDITION_ID = "COND_HH_무주택세대"


def load_corrections() -> dict:
    return json.loads(CORRECTIONS_PATH.read_text(encoding="utf-8"))


def apply_policy_corrections(policies: list, corrections: dict) -> None:
    """정책 노드의 설명 문구와 지역 문자열을 교정한다."""
    by_id = {policy["policy_id"]: policy for policy in policies}
    for item in corrections["text_corrections"]:
        policy = by_id[item["policy_id"]]
        field = item["field"]
        assert policy[field].count(item["find"]) == 1, f"교정 대상 문구 불일치: {item['policy_id']} {item['find']}"
        policy[field] = policy[field].replace(item["find"], item["replace"])
    for item in corrections["region_corrections"]:
        policy = by_id[item["policy_id"]]
        assert policy["region"] == item["before"], f"지역 교정 전 값 불일치: {item['policy_id']}"
        policy["region"] = item["after"]


def apply_region_to_chunks(chunks: list, corrections: dict) -> int:
    """지역 교정 정책의 Qdrant payload region을 맞춘다. 본문은 그대로라 벡터는 재계산할 필요가 없다."""
    after = {item["policy_id"]: item for item in corrections["region_corrections"]}
    changed = 0
    for chunk in chunks:
        item = after.get(chunk["policy_id"])
        if item:
            assert chunk["region"] == item["before"], f"청크 지역 교정 전 값 불일치: {chunk['chunk_id']}"
            chunk["region"] = item["after"]
            changed += 1
    return changed


def _household_group(policy_id: str) -> str:
    return f"GRP_{policy_id}_OR_HH"


def removed_group_ids(corrections: dict) -> set:
    """API 가구 항목 전체 선택에서 잘못 만들어진 가구유형 OR 그룹."""
    return {_household_group(item["policy_id"]) for item in corrections["household_condition_removals"]}


def apply_relationship_corrections(relationships: list, corrections: dict) -> tuple:
    """잘못 연결된 조건 관계를 제거하고 지역 관계를 교정한다. (교정된 목록, 제거한 관계) 반환."""
    groups = removed_group_ids(corrections)
    remove = set()
    for item in corrections["household_condition_removals"]:
        policy_id = item["policy_id"]
        root = f"GRP_{policy_id}_ROOT"
        remove.add(("CONTAINS_GROUP", root, _household_group(policy_id)))
        if item["remove_homeless_requirement"]:
            remove.add(("REQUIRES_CONDITION", policy_id, HOMELESS_CONDITION_ID))
            remove.add(("CONTAINS_CONDITION", root, HOMELESS_CONDITION_ID))
    for item in corrections["age_condition_removals"]:
        remove.add(("REQUIRES_CONDITION", item["policy_id"], item["condition_id"]))
        remove.add(("CONTAINS_CONDITION", f"GRP_{item['policy_id']}_ROOT", item["condition_id"]))

    household_policies = {f"{item['policy_id']}" for item in corrections["household_condition_removals"]}
    region_by_policy = {item["policy_id"]: item for item in corrections["region_corrections"]}
    seen_regions = set()
    kept, removed, matched = [], [], set()
    for row in relationships:
        key = (row["relation"], row["source_id"], row["target_id"])
        is_group_member = row["relation"] == "CONTAINS_CONDITION" and row["source_id"] in groups
        # 그룹 안의 7개 가구유형은 정책에서 직접 연결한 REQUIRES_CONDITION도 함께 끊는다
        is_household_requirement = (
            row["relation"] == "REQUIRES_CONDITION" and row["source_id"] in household_policies
            and row["target_id"].startswith(HOUSEHOLD_CONDITION_PREFIX) and row["target_id"] != HOMELESS_CONDITION_ID
        )
        if key in remove or is_group_member or is_household_requirement:
            matched.add(key)
            removed.append({"source_id": row["source_id"], "relation": row["relation"], "target_id": row["target_id"]})
            continue
        if row["relation"] == "AVAILABLE_IN" and row["source_id"] in region_by_policy:
            item = region_by_policy[row["source_id"]]
            assert row["target_id"] == item["before_region_id"], f"지역 관계 교정 전 값 불일치: {row['source_id']}"
            row = {**row, "target_id": item["after_region_id"], "target_name": item["after"]}
            seen_regions.add(row["source_id"])
        kept.append(row)
    missing = remove - matched
    assert not missing, f"제거 대상 관계를 찾지 못함: {sorted(missing)[:5]}"
    assert seen_regions == set(region_by_policy), "지역 교정 대상 관계 누락"
    return kept, removed


def apply_group_corrections(groups: list, corrections: dict) -> list:
    remove = removed_group_ids(corrections)
    assert remove <= {group["group_id"] for group in groups}, "제거 대상 조건 그룹을 찾지 못함"
    return [group for group in groups if group["group_id"] not in remove]
