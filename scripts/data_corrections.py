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
# 공식 신청기간이 확인되지 않은 정책의 application_period 값. 화면에서는 "신청 일정 확인 필요"로 보여 준다(#107).
UNVERIFIED_APPLICATION_PERIOD = "미확인"
# 수집 코드가 신청기간 칸이 비었을 때 넣던 기본 문구. 공식 원문이 아니라서 "상시 신청"으로 읽히면 안 된다.
COLLECTION_DEFAULT_PERIODS = frozenset({"상시/공고참조", "상시 또는 공고 확인", "상시/별도공고"})
# 저장된 reception_status는 현재 접수 상태가 아니라 일정 유형이다. Checker·화면은 이 값을 읽지 않는다.
STORED_RECEPTION_ALWAYS = "apply_always"
STORED_RECEPTION_PERIOD = "apply_period"
STORED_RECEPTION_UNKNOWN = "reception_unknown"


def load_corrections() -> dict:
    return json.loads(CORRECTIONS_PATH.read_text(encoding="utf-8"))


def omit_outdated_line(text: str, line: str) -> str:
    """검색용 본문에서 확인된 옛 행 하나만 뺀다. 나머지 문장과 순서는 그대로 둔다."""
    lines = text.splitlines(keepends=True)
    matches = [index for index, current in enumerate(lines) if current.rstrip("\r\n") == line]
    if len(matches) != 1:
        raise ValueError("교정 대상 문서에서 옛 안내 행을 한 번만 찾을 수 있어야 합니다")
    del lines[matches[0]]
    return "".join(lines)


def rewrite_line(text: str, item: dict) -> str:
    """검색용 본문의 한 행을 교정 기록대로 바꾼다(document_line_rewrites, #107).

    정부24 본문의 '- 신청기한: …' 같은 행은 수집 코드가 붙인 칸 이름 + 원문 값이다.
    칸 이름에 무엇의 기한인지 적거나, 지난 예고 문구만 빼는 용도로만 쓴다.
    - kept_text: 원문에서 그대로 남겨야 하는 값. 바꾸기 전·후 행에 모두 있어야 한다.
    - dropped_text: 빼는 원문 문구(선택). 바꾸기 전 행에만 있고, 바꾼 행에는 없어야 한다.
    """
    line, replacement = item["line"], item["replacement"]
    if item["kept_text"] not in line or item["kept_text"] not in replacement:
        raise ValueError(f"행 교정은 원문 값을 그대로 남겨야 합니다: {item['document_id']}")
    dropped = item.get("dropped_text")
    if dropped and (dropped not in line or dropped in replacement):
        raise ValueError(f"행 교정에서 뺄 문구를 확인하세요: {item['document_id']}")
    lines = text.splitlines(keepends=True)
    matches = [index for index, current in enumerate(lines) if current.rstrip("\r\n") == line]
    if len(matches) != 1:
        raise ValueError(f"교정할 행을 한 번만 찾을 수 있어야 합니다: {item['document_id']}")
    ending = lines[matches[0]][len(line):]
    lines[matches[0]] = replacement + ending
    return "".join(lines)


def omit_held_lines(text: str, lines: list[str]) -> str:
    """보류한 FAQ 행을 검색용 본문에서 뺀다. 행 사이 빈 줄은 하나만 남기고, 나머지 문장과 순서는 그대로 둔다."""
    for line in lines:
        text = omit_outdated_line(text, line)
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    return text.strip()


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


# 실제 신청·예약 화면까지 들어가 본 단계. 신청 버튼이나 안내만 본 경우는 확인 완료로 올리지 않는다.
VERIFIED_APPLICATION_STAGES = frozenset({"booking_screen", "application_step", "identity_check"})
APPLICATION_STAGES = VERIFIED_APPLICATION_STAGES | {"apply_button_out_of_period"}


def apply_application_url_corrections(policies: list, reviews: list, baseline_policies: list,
                                      corrections: dict) -> list:
    """원천 신청 필드 판정 뒤, 실제 화면을 열어 확인한 신청 주소 교정을 적용한다.

    after가 있으면 그 서비스 전용 신청 경로를 직접 연 주소이고, None이면 신청 화면이 아니라서 비운다.
    어디까지 봤는지는 verified_stage에 적고, 실제 신청·예약 단계까지 본 경우만 live_application_page_verified를 true로 둔다.
    수정 전 값이 다르면 빌드를 멈춘다. 기존 정책의 변경 목록(변경 전후)은 교정 뒤 값으로 다시 만든다.
    """
    by_id = {policy["policy_id"]: policy for policy in policies}
    review_by_id = {row["policy_id"]: row for row in reviews}
    items = corrections.get("application_url_corrections", [])
    assert len({item["policy_id"] for item in items}) == len(items), "신청 주소 교정 대상 중복"
    for item in items:
        policy = by_id[item["policy_id"]]
        assert policy.get("official_apply_url") == item["before"], f"신청 주소 교정 전 값 불일치: {item['policy_id']}"
        stage = item.get("verified_stage")
        if item["after"] is None:
            assert stage is None, f"비운 신청 주소에 확인 단계가 있음: {item['policy_id']}"
        else:
            assert stage in APPLICATION_STAGES, f"신청 주소의 확인 단계가 없거나 알 수 없음: {item['policy_id']}"
        policy["official_apply_url"] = item["after"]
        review = review_by_id[item["policy_id"]]
        review.update({
            "official_apply_url": item["after"],
            "reason": f"manual_review_{item['reason']}",
            "live_application_page_verified": stage in VERIFIED_APPLICATION_STAGES,
            "manual_review_stage": stage,
            "manual_review_note": item["evidence"],
            "manual_review_checked_at": item["checked_at"],
        })
    changes = []
    for baseline in baseline_policies:
        current = by_id[baseline["policy_id"]]
        if baseline.get("official_apply_url") != current.get("official_apply_url"):
            changes.append({
                "policy_id": baseline["policy_id"],
                "field": "official_apply_url",
                "before": baseline.get("official_apply_url"),
                "after": current.get("official_apply_url"),
                "reason": review_by_id[baseline["policy_id"]]["reason"],
            })
    return changes


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
    ongoing = set(corrections["application_ongoing"])
    assert set(dates) <= known and set(notes) <= known, "신청 기간 교정 대상 정책 불일치"
    assert ongoing <= known, f"상시 접수 교정 대상 정책 불일치: {sorted(ongoing - known)}"
    assert set(dates) & set(notes) <= set(corrections["application_notes_with_dates"]), "날짜와 확인 메모는 허용된 정책만 함께 쓴다"
    policies_by_id = {policy["policy_id"]: policy for policy in policies}
    for entry in corrections.get("unverified_application_periods", []):
        policy_id = entry["policy_id"]
        policy = policies_by_id.get(policy_id)
        if (policy is None or policy.get("application_period") != entry["before"]
                or policy_id in dates or policy_id in ongoing or policy_id not in notes):
            raise ValueError(f"미확인 신청기간 교정 전 값·근거 불일치: {policy_id}")
        policy["application_period"] = "미확인"
        for key in ("application_start", "application_end", "application_is_ongoing"):
            policy.pop(key, None)
    for policy in policies:
        item = dates.get(policy["policy_id"], {})
        if "start" in item:
            policy["application_start"] = item["start"]
        if "end" in item:
            policy["application_end"] = item["end"]
        if policy["policy_id"] in notes:
            policy["application_status_note"] = notes[policy["policy_id"]]
        if policy["policy_id"] in ongoing:
            assert "end" not in item, f"종료일과 상시 접수를 함께 쓸 수 없음: {policy['policy_id']}"
            policy["application_is_ongoing"] = True


def replace_default_application_periods(policies: list) -> list[str]:
    """수집 기본 문구("상시/공고참조" 등)를 "미확인"으로 바꾸고 바꾼 정책 ID를 돌려준다.

    공식 일정으로 교정한 정책은 이미 다른 값이라 그대로 둔다. 기본 문구인데 날짜나 상시 값이 있으면
    어느 쪽이 맞는지 알 수 없으므로 빌드를 멈춘다. 종료일 없이 "~ 상시"로 끝나는 값도 상시 근거가 없으면 멈춘다.
    """
    replaced = []
    for policy in policies:
        period = (policy.get("application_period") or "").strip()
        if period in COLLECTION_DEFAULT_PERIODS:
            if any(policy.get(key) for key in ("application_start", "application_end", "application_is_ongoing")):
                raise ValueError(f"기본 신청기간 문구와 날짜·상시 값이 함께 있음: {policy['policy_id']}")
            policy["application_period"] = UNVERIFIED_APPLICATION_PERIOD
            replaced.append(policy["policy_id"])
        elif period.endswith("~ 상시") and policy.get("application_is_ongoing") is not True:
            raise ValueError(f"상시 근거 없이 '~ 상시'로 끝나는 신청기간: {policy['policy_id']}")
    return replaced


def check_stored_reception_status(policies: list) -> None:
    """저장된 reception_status가 일정 값(상시·날짜)과 맞는지 확인한다. 맞지 않으면 빌드를 멈춘다."""
    for policy in policies:
        if "reception_status" not in policy:
            continue
        if policy.get("application_is_ongoing") is True:
            expected = STORED_RECEPTION_ALWAYS
        elif policy.get("application_start") or policy.get("application_end"):
            expected = STORED_RECEPTION_PERIOD
        else:
            expected = STORED_RECEPTION_UNKNOWN
        if policy["reception_status"] != expected:
            raise ValueError(
                f"저장 reception_status 불일치: {policy['policy_id']} {policy['reception_status']} (기대 {expected})")


def apply_taxonomy_to_chunks(chunks: list, policies: list, corrections: dict) -> int:
    """청크의 분야를 정책과 맞추고, 지정 정책의 life_stages를 정책의 표준 생애단계로 맞춘다. 본문은 그대로다."""
    by_id = {policy["policy_id"]: policy for policy in policies}
    category_policies = {item["policy_id"] for item in corrections["category_corrections"]}
    align = set(corrections["chunk_life_stage_alignment_policies"])
    # 정책명을 교정한 정책은 청크의 policy_name도 맞춘다. 임베딩 입력의 정책명 줄이 청크 메타데이터에서 오기 때문이다.
    align_names = set(corrections.get("chunk_policy_name_alignment_policies", []))
    changed = 0
    for chunk in chunks:
        policy = by_id[chunk["policy_id"]]
        before = (chunk["category"], chunk["life_stages"], chunk.get("policy_name"))
        if chunk["policy_id"] in category_policies:
            chunk["category"] = policy["category"]
        if chunk["policy_id"] in align:
            chunk["life_stages"] = list(policy["life_stages"])
        if chunk["policy_id"] in align_names:
            chunk["policy_name"] = policy["policy_name"]
        changed += before != (chunk["category"], chunk["life_stages"], chunk.get("policy_name"))
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


def _condition_removals(corrections: dict) -> list:
    """소득 조건 교정과 특수 조건 제거(우대·순위 조건을 필수에서 빼기)는 같은 모양이라 함께 처리한다."""
    return corrections["income_condition_corrections"] + corrections.get("special_condition_removals", [])


def removed_group_ids(corrections: dict) -> set:
    """API 가구 항목 전체 선택에서 잘못 만들어진 가구유형 OR 그룹과, 통째로 지우는 소득·특수 조건 그룹."""
    groups = {_household_group(item["policy_id"]) for item in corrections["household_condition_removals"]}
    return groups | {item["group_id"] for item in _condition_removals(corrections) if item["action"] == "remove"}


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

    for item in _condition_removals(corrections):
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
    # review_status는 기본 extracted. 근거 문서가 아직 후보에 없는 조건은 교정 항목에 needs_review를 적어
    # 검색 계층이 자동 판정을 보류하게 한다(QUALITY_NEEDS_REVIEW).
    new_rows = [{"source_id": item["group_id"], "relation": "CONTAINS_CONDITION", "target_id": item["condition_id"],
                 "target_name": item["target_name"], "evidence_document_id": item["evidence_document_id"],
                 "evidence_location": item["evidence_location"], "review_status": item.get("review_status", "extracted")}
                for item in corrections["group_condition_additions"]]
    new_rows += [{"source_id": item["source_id"], "relation": "CONTAINS_GROUP", "target_id": item["target_id"],
                  "target_name": item["target_name"], "evidence_document_id": item["evidence_document_id"],
                  "evidence_location": item["evidence_location"], "review_status": "extracted"}
                 for item in corrections["group_group_additions"]]
    # 조건 트리에 새로 넣은 조건은 정책-조건 직접 관계(REQUIRES_CONDITION)도 함께 이어야 한다.
    # 검색 계층이 정책의 REQUIRES 목록과 트리 안 조건 목록이 같은지 확인하기 때문이다.
    existing = {(row["source_id"], row["target_id"]) for row in kept if row["relation"] == "REQUIRES_CONDITION"}
    for item in corrections.get("requires_condition_additions", []):
        assert (item["policy_id"], item["condition_id"]) not in existing, \
            f"이미 있는 REQUIRES 관계를 다시 추가함: {item['policy_id']} {item['condition_id']}"
        new_rows.append({"source_id": item["policy_id"], "relation": "REQUIRES_CONDITION", "target_id": item["condition_id"],
                         "target_name": item["target_name"], "evidence_document_id": item["evidence_document_id"],
                         "evidence_location": item["evidence_location"],
                         "review_status": item.get("review_status", "extracted")})
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
