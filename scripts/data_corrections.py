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
SOURCE_REVIEW_ATTRIBUTES = ("income", "household")
SOURCE_REVIEW_FIELDS = tuple(f"{attribute}_{suffix}" for attribute in SOURCE_REVIEW_ATTRIBUTES
                             for suffix in ("source_review", "source_note", "followup", "check_point")) + (
    "application_start", "application_end", "application_status_note", "application_is_ongoing")


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
    for item in corrections["category_corrections"]:
        policy = by_id[item["policy_id"]]
        assert policy["category"] == item["before"], f"분야 교정 전 값 불일치: {item['policy_id']}"
        policy["category"] = item["after"]
    codes = corrections["target_group_code_names"]
    for policy in policies:
        policy["target_groups"] = [codes.get(value, value) for value in policy["target_groups"]]
    for item in corrections["life_stage_corrections"]:
        policy = by_id[item["policy_id"]]
        assert sorted(policy["life_stages"]) == sorted(item["before"]), f"생애단계 교정 전 값 불일치: {item['policy_id']}"
        policy["life_stages"] = list(item["after"])


def apply_source_review(policies: list, corrections: dict) -> None:
    """정책 노드에 소득·가구 항목별 원문 확인 결과를 남긴다. 값이 없는 속성은 만들지 않는다."""
    review = corrections["source_review"]
    known = {policy["policy_id"] for policy in policies}
    for attribute in SOURCE_REVIEW_ATTRIBUTES:
        assert set(review[attribute]) <= known, f"원문 확인 대상 정책 불일치: {attribute}"
    for policy in policies:
        for attribute in SOURCE_REVIEW_ATTRIBUTES:
            item = review[attribute].get(policy["policy_id"], {})
            policy[f"{attribute}_source_review"] = item.get("status", "not_reviewed")
            for field, suffix in (("note", "source_note"), ("followup", "followup"), ("check_point", "check_point")):
                if field in item:
                    policy[f"{attribute}_{suffix}"] = item[field]
    dates, notes = corrections["application_dates"], corrections["application_status_notes"]
    assert set(dates) <= known and set(notes) <= known, "신청 기간 교정 대상 정책 불일치"
    assert set(dates) & set(notes) <= set(corrections["application_notes_with_dates"]), "날짜와 확인 메모는 허용된 정책만 함께 쓴다"
    for policy in policies:
        item = dates.get(policy["policy_id"], {})
        if "start" in item:
            policy["application_start"] = item["start"]
        if "end" in item:
            policy["application_end"] = item["end"]
        if policy["policy_id"] in notes:
            policy["application_status_note"] = notes[policy["policy_id"]]
        if policy["policy_id"] in corrections["application_ongoing"]:
            assert "end" not in item, f"종료일과 상시 접수를 함께 쓸 수 없음: {policy['policy_id']}"
            policy["application_is_ongoing"] = True


def apply_taxonomy_to_chunks(chunks: list, policies: list, corrections: dict) -> int:
    """청크의 분야를 정책과 맞추고, 지정 정책의 life_stages를 정책의 표준 생애단계로 맞춘다. 본문은 그대로다."""
    by_id = {policy["policy_id"]: policy for policy in policies}
    category_policies = {item["policy_id"] for item in corrections["category_corrections"]}
    align = set(corrections["chunk_life_stage_alignment_policies"])
    changed = 0
    for chunk in chunks:
        policy = by_id[chunk["policy_id"]]
        before = (chunk["category"], chunk["life_stages"])
        if chunk["policy_id"] in category_policies:
            chunk["category"] = policy["category"]
        if chunk["policy_id"] in align:
            chunk["life_stages"] = list(policy["life_stages"])
        changed += before != (chunk["category"], chunk["life_stages"])
    return changed


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
    groups = {_household_group(item["policy_id"]) for item in corrections["household_condition_removals"]}
    return groups | {item["group_id"] for item in corrections["income_condition_corrections"] if item["action"] == "remove"}


def apply_relationship_corrections(relationships: list, corrections: dict) -> tuple:
    """잘못 연결된 조건 관계를 제거·교체하고 지역 관계를 교정한다. (교정된 목록, 제거한 관계, 추가한 관계) 반환."""
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

    for item in corrections["income_condition_corrections"]:
        if item["action"] == "remove":
            remove.add(("CONTAINS_GROUP", f"GRP_{item['policy_id']}_ROOT", item["group_id"]))
            remove.update(("REQUIRES_CONDITION", item["policy_id"], condition_id) for condition_id in item["before"])
        elif item["action"] == "remove_condition":
            for condition_id in item["before"]:
                remove.add(("REQUIRES_CONDITION", item["policy_id"], condition_id))
                remove.add(("CONTAINS_CONDITION", item["group_id"], condition_id))
    for item in corrections["life_stage_corrections"]:
        for stage in set(item["before"]) - set(item["after"]) - {"unknown"}:
            remove.add(("APPLIES_TO_LIFESTAGE", item["policy_id"], f"LIFESTAGE_{stage}"))

    # 연령 교체: 원문에 나이가 분명한 정책은 조건을 지우지 않고 원문 값으로 바꾼다
    replacements = {}
    for item in corrections["age_condition_replacements"]:
        replacements[("REQUIRES_CONDITION", item["policy_id"], item["before"])] = item
        replacements[("CONTAINS_CONDITION", f"GRP_{item['policy_id']}_ROOT", item["before"])] = item

    household_policies = {f"{item['policy_id']}" for item in corrections["household_condition_removals"]}
    region_by_policy = {item["policy_id"]: item for item in corrections["region_corrections"]}
    seen_regions = set()
    kept, removed, added, matched = [], [], [], set()
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
        if key in replacements:
            matched.add(key)
            item = replacements[key]
            removed.append({"source_id": row["source_id"], "relation": row["relation"], "target_id": row["target_id"]})
            row = {**row, "target_id": item["after"], "target_name": item["after_name"], "evidence_location": "원문 대조 교정"}
            added.append({"source_id": row["source_id"], "relation": row["relation"], "target_id": row["target_id"]})
        if row["relation"] == "AVAILABLE_IN" and row["source_id"] in region_by_policy:
            item = region_by_policy[row["source_id"]]
            assert row["target_id"] == item["before_region_id"], f"지역 관계 교정 전 값 불일치: {row['source_id']}"
            row = {**row, "target_id": item["after_region_id"], "target_name": item["after"]}
            seen_regions.add(row["source_id"])
        kept.append(row)
    missing = (remove | set(replacements)) - matched
    assert not missing, f"제거 대상 관계를 찾지 못함: {sorted(missing)[:5]}"
    assert seen_regions == set(region_by_policy), "지역 교정 대상 관계 누락"
    # 원문 근거로 새로 잇는 관계. 제외 조건은 NOT 그룹 안에만 둔다
    # (REQUIRES_CONDITION으로 이으면 "공무원이어야 한다"는 반대 뜻이 되기 때문이다)
    new_rows = [{"source_id": item["group_id"], "relation": "CONTAINS_CONDITION", "target_id": item["condition_id"],
                 "target_name": item["target_name"], "evidence_document_id": item["evidence_document_id"],
                 "evidence_location": item["evidence_location"], "review_status": "extracted"}
                for item in corrections["group_condition_additions"]]
    new_rows += [{"source_id": item["source_id"], "relation": "CONTAINS_GROUP", "target_id": item["target_id"],
                  "target_name": item["target_name"], "evidence_document_id": item["evidence_document_id"],
                  "evidence_location": item["evidence_location"], "review_status": "extracted"}
                 for item in corrections["group_group_additions"]]
    for item in corrections["life_stage_corrections"]:
        for stage in [value for value in item["after"] if value not in item["before"]]:
            new_rows.append({"source_id": item["policy_id"], "relation": "APPLIES_TO_LIFESTAGE",
                             "target_id": f"LIFESTAGE_{stage}", "target_name": stage,
                             "evidence_document_id": item["policy_id"].replace("POL_", "DOC_", 1),
                             "evidence_location": item.get("evidence_location", "원문 대조 교정"), "review_status": "extracted"})
    kept.extend(new_rows)
    added.extend({"source_id": row["source_id"], "relation": row["relation"], "target_id": row["target_id"]} for row in new_rows)
    return kept, removed, added


def add_condition_nodes(path: Path, corrections: dict) -> None:
    """원문 값으로 교체할 때 필요한 연령 조건 노드를 후보의 조건 노드 파일에 덧붙인다."""
    with open(path, "a", encoding="utf-8", newline=chr(10)) as f:
        for node in corrections["new_condition_nodes"]:
            f.write(json.dumps(node, ensure_ascii=False) + chr(10))


def apply_group_corrections(groups: list, corrections: dict) -> list:
    remove = removed_group_ids(corrections)
    assert remove <= {group["group_id"] for group in groups}, "제거 대상 조건 그룹을 찾지 못함"
    kept = [dict(group) for group in groups if group["group_id"] not in remove]
    by_id = {group["group_id"]: group for group in kept}
    for item in corrections["group_description_corrections"]:
        group = by_id[item["group_id"]]
        assert group["description"].startswith(item["before_prefix"]), f"그룹 설명 불일치: {item['group_id']}"
        group["description"] = item["after"]
    for item in corrections["income_condition_corrections"]:
        if item["action"] == "mark_unknown":
            group = by_id[item["group_id"]]
            assert group["logic"] == "OR", f"소득 그룹 논리 불일치: {item['group_id']}"
            group["logic"] = "UNKNOWN"
            group["description"] = f"{group['description']} (자동 판정 보류: {item['evidence']})"
    for item in corrections["empty_group_logic_corrections"]:
        group = by_id[item["group_id"]]
        assert group["logic"] == item["before"], f"빈 그룹 논리 불일치: {item['group_id']}"
        group["logic"] = item["after"]
    return kept + [dict(group) for group in corrections["new_condition_groups"]]
