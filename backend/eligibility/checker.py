
"""SSOK 자격 조건 판정기 (ask_contract v1.0).

입력은 Hybrid 검색 결과의 정책 객체 한 건이다.
DB/LLM 호출이나 입력 변형을 하지 않는다.
조건의 'eligible'은 개별/논리 조건의 충족일 뿐
정책 신청 자격 보증이 아니다.
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime, timedelta, timezone

from database.eligibility_fields import ALL_ELIGIBILITY_FIELDS, SCOPED_ELIGIBILITY_FIELDS

KST = timezone(timedelta(hours=9))

OK, NO, CHECK = "eligible", "ineligible", "needs_check"

_LABELS = {
    "age": "나이",
    "region": "거주 지역",
    "income": "소득",
    "housing": "가구·주거 조건",
    "children": "자녀 월령",
    "eligibility": "자격 요건",
}

_GROUP_REASONS = {
    "AND": "모든 조건을 충족해야 합니다(종합 결과).",
    "OR": "조건 중 하나 이상 충족해야 합니다(종합 결과).",
    "NOT": "해당 조건이 충족되지 않아야 합니다(종합 결과).",
}

MAX_VALID_AGE = 120
NO_UPPER_LIMIT_SENTINEL = 120


def _is_valid_age(age: object) -> bool:
    """사용자 만 나이가 유효한 범위(0~120)인지 확인한다."""
    return (
        isinstance(age, int)
        and not isinstance(age, bool)
        and 0 <= age <= MAX_VALID_AGE
    )


def _is_valid_age_range(low: int, high: int) -> bool:
    """추출 데이터가 허용하는 연령 구간의 순서·범위를 확인한다."""
    return 0 <= low <= high <= MAX_VALID_AGE


def _has_no_upper_limit(low: int, high: int) -> bool:
    """상한 120 관례를 무제한으로 표현할지 결정한다.

    0~120은 광범위한 추출 범위이므로 자동으로
    연령 무제한이라 단정하지 않는다.
    """
    return low > 0 and high == NO_UPPER_LIMIT_SENTINEL


_AGE_RANGE = re.compile(
    r"^\s*(\d{1,3})\s*(?:~|～|–|—|-)\s*(\d{1,3})\s*$"
)


def _result(
    field: str,
    status: str,
    reason: str,
    label: str | None = None,
) -> dict:
    return {
        "field": field,
        "label": label or _LABELS.get(field, "기타 자격 조건"),
        "status": status,
        "reason": reason,
    }


def _field(condition: dict) -> str:
    kind = str(condition.get("type") or "").upper()

    if kind == "AGE":
        return "age"

    if kind in {"REGION", "RESIDENCE", "LOCATION"}:
        return "region"

    if kind == "INCOME":
        return "income"

    if (
        kind == "SPECIAL_TARGET"
        and str(condition.get("unit") or "").strip() == "개월"
    ):
        return "children"

    return {
        "EMPLOYMENT": "employment",
        "EDUCATION": "education",
        "HOUSING_HOUSEHOLD": "housing",
        "SPECIAL_TARGET": "target",
    }.get(kind, "eligibility")


def _condition(condition: dict, profile: dict) -> dict:
    field = _field(condition)

    if field == "children":
        # 자녀 월령은 신청자의 age 필드와 혼용하지 않는다.
        # MVP에서는 미입력 시 needs_check.
        raw = str(condition.get("value") or "").strip()
        operator = str(
            condition.get("operator") or ""
        ).strip().upper()

        # LT / 24개월 미만만 자동 비교한다.
        match = re.fullmatch(
            r"(\d{1,3})\s*(?:개월)?", raw
        )

        if (
            operator != "LT"
            or match is None
            or int(match.group(1)) <= 0
        ):
            return _result(
                "children",
                CHECK,
                "자녀 월령 조건의 정확한 비교 기준을 확인해야 합니다.",
            )

        limit = int(match.group(1))
        months = profile.get("child_age_months")

        if months is None:
            return _result(
                "children",
                CHECK,
                f"자녀 월령(개월 수)을 확인해야 합니다. "
                f"기준: {limit}개월 미만.",
            )

        if type(months) is not int or months < 0:
            return _result(
                "children",
                CHECK,
                "유효한 자녀 월령을 확인할 수 없습니다.",
            )

        status = OK if months < limit else NO

        return _result(
            "children",
            status,
            f"자녀 월령 {limit}개월 미만 기준에 "
            f"입력한 {months}개월은 "
            + (
                "포함됩니다."
                if status == OK
                else "포함되지 않습니다."
            ),
        )

    if field == "age":
        age = profile.get("age")

        if age is None:
            return _result(
                field,
                CHECK,
                "사용자의 만 나이가 입력되지 않았습니다.",
            )

        if not _is_valid_age(age):
            return _result(
                field,
                CHECK,
                "유효한 만 나이를 확인할 수 없습니다.",
            )

        match = _AGE_RANGE.fullmatch(
            str(condition.get("value") or "")
        )

        if (
            condition.get("operator") != "BETWEEN"
            or not match
        ):
            return _result(
                field,
                CHECK,
                "연령 조건 형식을 해석할 수 없어 "
                "원문 확인이 필요합니다.",
            )

        low, high = map(int, match.groups())

        if not _is_valid_age_range(low, high):
            return _result(
                field,
                CHECK,
                "연령 범위가 유효하지 않아 확인이 필요합니다.",
            )

        status = OK if low <= age <= high else NO

        if low == 0 and high == NO_UPPER_LIMIT_SENTINEL:
            return _result(
                field,
                status,
                f"입력한 만 {age}세는 데이터에 저장된 "
                "연령 범위에 포함됩니다. "
                "다만 실제 적용 연령 제한은 "
                "공식 공고로 확인해야 합니다.",
            )

        age_rule = (
            f"만 {low}세 이상(상한 없음)"
            if _has_no_upper_limit(low, high)
            else f"만 {low}~{high}세"
        )

        return _result(
            field,
            status,
            f"{age_rule} 기준에 입력한 만 {age}세가 "
            + (
                "포함됩니다."
                if status == OK
                else "포함되지 않습니다."
            ),
        )

    if field == "region":
        return _result(
            field,
            CHECK,
            "지역 범위·거주 요건의 공식 근거 확인이 필요합니다.",
        )

    if field == "income":
        return _result(
            field,
            CHECK,
            "개인/가구 소득 및 중위소득 비교 기준을 확인해야 합니다.",
        )

    return _result(
        field,
        CHECK,
        f"{condition.get('name') or condition.get('type') or '이 조건'}"
        "은(는) 추가 확인이 필요합니다.",
    )


def _combine(logic: str, states: list[str]) -> str:
    """보수적 3값 논리. 비어 있는 조건은 언제나 미확인."""

    if not states:
        return CHECK

    if logic == "AND":
        return (
            NO if NO in states
            else OK if all(x == OK for x in states)
            else CHECK
        )

    if logic == "OR":
        return (
            OK if OK in states
            else NO if all(x == NO for x in states)
            else CHECK
        )

    if logic == "NOT":
        if len(states) != 1:
            return CHECK

        return {
            OK: NO,
            NO: OK,
            CHECK: CHECK,
        }[states[0]]

    return CHECK


def _walk(
    group: dict,
    profile: dict,
    depth: int = 0,
    *,
    issues: set[str] | None = None,
) -> tuple[str, list[dict]]:
    """조건 트리를 한 번만 순회하며 결과와 구조 오류를 수집한다."""

    if (
        depth > 10
        or not isinstance(group, dict)
        or group.get("cycle")
        or group.get("not_loaded")
    ):
        if issues is not None:
            issues.add("invalid_group")
        return CHECK, [
            _result(
                "eligibility",
                CHECK,
                "조건 그룹이 누락·순환되었거나 "
                "깊이 제한을 초과했습니다.",
            )
        ]

    logic = str(
        group.get("logic") or "UNKNOWN"
    ).upper()

    if logic not in {"AND", "OR", "NOT"}:
        if issues is not None:
            issues.add("unknown_logic")
        return CHECK, [
            _result(
                "eligibility",
                CHECK,
                "UNKNOWN 조건 그룹은 자동 판정할 수 없습니다.",
            )
        ]

    items = []
    states = []

    conditions = group.get("conditions")
    children = group.get("groups")

    if (
        not isinstance(conditions, list)
        or not isinstance(children, list)
    ):
        if issues is not None:
            issues.add("invalid_group")
        return CHECK, [
            _result(
                "eligibility",
                CHECK,
                "조건 그룹의 내부 구조가 올바르지 않습니다.",
            )
        ]

    for condition in conditions:
        if not isinstance(condition, dict):
            if issues is not None:
                issues.add("invalid_condition")
            item = _result(
                "eligibility",
                CHECK,
                "해석할 수 없는 조건 형식입니다.",
            )
        else:
            item = _condition(condition, profile)

        items.append(item)
        states.append(item["status"])

    for child in children:
        status, nested = _walk(
            child,
            profile,
            depth + 1,
            issues=issues,
        )

        states.append(status)
        items.extend(nested)

    group_status = _combine(logic, states)

    # 결과가 프론트 및 LLM에 그대로 전달되므로 내부 group_id는 노출하지 않는다.
    items.append(
        _result(
            "eligibility",
            group_status,
            _GROUP_REASONS[logic],
        )
    )

    return group_status, items


def _affected_fields(
    quality: dict,
    eligibility: dict,
    graph_missing: bool,
) -> set[str]:

    if graph_missing:
        return {"all"}

    if (
        eligibility.get("unknown_logic_groups")
        or eligibility.get("structure_issues")
    ):
        return {"all"}

    if not quality.get("hold_auto_eligibility"):
        return set()

    attrs = quality.get("affected_attributes")

    if not isinstance(attrs, list) or not attrs:
        return {"all"}

    aliases = {
        "residence": "region",
        "location": "region",
        "housing_household": "housing",
        "household": "housing",
    }

    # 검색 계층·Pipeline과 같은 공통 상수. 판단(별칭 변환·전체 보류)은 이 함수가 그대로 맡는다.
    supported = SCOPED_ELIGIBILITY_FIELDS | {ALL_ELIGIBILITY_FIELDS}

    normalized = set()

    for attr in attrs:
        if not isinstance(attr, str) or not attr.strip():
            return {"all"}

        name = attr.strip().lower()
        field = aliases.get(name, name)

        if field not in supported:
            return {"all"}

        normalized.add(field)

    return normalized or {"all"}


def _safe_review_text(value: object) -> str:
    """검토 메모는 문자열만 사용하고 비정상 입력은 제외한다."""
    if not isinstance(value, str):
        return ""

    return " ".join(value.split())[:300]


def _source_review_conditions(
    policy_node: dict,
    profile: dict,
) -> list[dict]:
    """소득·가구 원문 검토 정보를 기존 reason으로 전달한다.

    not_specified / not_reviewed / specified:
        별도 확인 항목 생성하지 않음

    needs_review:
        needs_check 항목 및 확인 사유 생성

    별도 followup 출력 키는 추가하지 않는다.
    """

    results = []

    for prefix, field, label in (
        ("income", "income", "소득 요건"),
        ("household", "housing", "가구 요건"),
    ):
        status = policy_node.get(
            f"{prefix}_source_review"
        )

        if status != "needs_review":
            continue

        followup = policy_node.get(
            f"{prefix}_followup"
        )

        if (
            not isinstance(followup, str)
            or followup not in {"ask_user", "check_notice"}
        ):
            followup = "check_notice"

        parts = [
            f"{label}의 공식 원문 기준을 "
            "추가 확인해야 합니다."
        ]

        check_point = _safe_review_text(
            policy_node.get(f"{prefix}_check_point")
        )

        source_note = _safe_review_text(
            policy_node.get(f"{prefix}_source_note")
        )

        if check_point:
            parts.append(
                f"확인할 항목: {check_point}"
            )

        if source_note:
            parts.append(
                f"원문 검토 메모: {source_note}"
            )

        if followup == "ask_user":
            if prefix == "income":
                income = profile.get("income")

                if (
                    type(income) in (int, float)
                    and income >= 0
                    and math.isfinite(income)
                ):
                    parts.append(
                        "입력된 소득에 적용할 공식 "
                        "비교 기준을 확인해 주세요."
                    )
                else:
                    parts.append(
                        "필요한 소득 정보를 추가로 "
                        "확인해 주세요."
                    )
            else:
                parts.append(
                    "가구 관련 추가 정보를 "
                    "답변 본문에서 확인해 주세요."
                )
        else:
            parts.append(
                "공식 공고의 해당 항목을 확인해 주세요."
            )

        results.append(
            _result(
                field,
                CHECK,
                " ".join(parts),
                label,
            )
        )

    return results


def _reception_status(policy: dict) -> str:
    """공식 날짜 및 상시 접수 여부로 신청 상태를 판정한다.

    날짜는 Hybrid 검색 결과의 graph.policy에서 읽는다.
    """

    graph = policy.get("graph")
    if not isinstance(graph, dict):
        return "unknown"

    policy_node = graph.get("policy")
    if not isinstance(policy_node, dict):
        return "unknown"

    start_value = policy_node.get("application_start")
    end_value = policy_node.get("application_end")
    ongoing_value = policy_node.get("application_is_ongoing")

    def parse_iso_date(
        value: object,
    ) -> tuple[date | None, bool]:

        if (
            value is None
            or (
                isinstance(value, str)
                and not value.strip()
            )
        ):
            return None, True

        if (
            not isinstance(value, str)
            or not re.fullmatch(
                r"\d{4}-\d{2}-\d{2}", value
            )
        ):
            return None, False

        try:
            return date.fromisoformat(value), True
        except ValueError:
            return None, False

    begin, start_valid = parse_iso_date(start_value)
    finish, end_valid = parse_iso_date(end_value)

    if not start_valid or not end_valid:
        return "unknown"

    if (
        begin is not None
        and finish is not None
        and begin > finish
    ):
        return "unknown"

    if ongoing_value is True and finish is not None:
        return "unknown"

    today = datetime.now(KST).date()

    if begin is not None and today < begin:
        return "before"

    if begin is not None and finish is not None:
        return (
            "closed"
            if today > finish
            else "open"
        )

    if finish is None and ongoing_value is True:
        return "open"

    return "unknown"



# #42: AGE 조건 미추출 안내는 설명 문구에 신청자 연령이 명시된 때만 추가한다.
# '세대'·'개월'은 숫자 연령으로 보지 않는다. 0~0세는 API 오류이므로 원문 확인 대상이다.
_AGE_IN_DESCRIPTION = re.compile(
    r"(?<!\d)(?:만\s*)?\d{1,3}\s*"
    r"(?:(?:~|～|–|—|-)\s*\d{1,3}\s*)?세(?!대)"
)
_CHILD_AGE_TERMS = (
    "자녀", "손자녀", "아이", "아동", "영유아", "영아", "유아",
    "어린이", "미성년자", "태아", "신생아",
)
_CHILD_AGE_PATTERN = "|".join(map(re.escape, _CHILD_AGE_TERMS))
_AGE_SUBJECT_MARKER = re.compile(
    rf"(?P<child>{_CHILD_AGE_PATTERN})"
    r"|(?P<applicant>신청자|신청인|지원자|본인|부모|보호자|청년)"
)
_AGE_CLAUSE_BOUNDARY = re.compile(r"[,;；。.!?？\n/|]")
_CHILD_AFTER_AGE = re.compile(
    r"^\s*(?:(?:이상|이하|미만|초과|부터|까지|의|인)\s*)*"
    rf"(?:{_CHILD_AGE_PATTERN})"
)


def _has_applicant_age_mention(
    description: str, *, has_child_condition: bool,
) -> bool:
    """자녀 나이가 아닌 신청자 나이 표현만 인정한다.

    절 단위로 가까운 주어를 판단한다. 자녀 조건이 있는데 주어 없는 나이
    표현만 있으면 신청자 나이라고 추측하지 않는다.
    """
    for clause in _AGE_CLAUSE_BOUNDARY.split(description):
        for match in _AGE_IN_DESCRIPTION.finditer(clause):
            last_subject = None
            for subject in _AGE_SUBJECT_MARKER.finditer(clause, 0, match.start()):
                last_subject = subject.lastgroup

            if last_subject == "child" or _CHILD_AFTER_AGE.match(clause[match.end():]):
                continue
            if has_child_condition and last_subject != "applicant":
                continue
            return True
    return False


def _should_add_missing_age_notice(
    policy_node: dict,
    affected: set[str],
    conditions: list[dict],
    walk_issues: set[str],
) -> bool:
    """실제 AGE 판정 누락만 안내한다. 기존 _walk 결과만 사용한다.

    구조 오류·UNKNOWN·전체 품질 보류는 이미 포괄적인 확인 안내가 있다.
    일반 소득·가구 원문 검토는 나이와 별개이므로 제외 조건이 아니다.
    """
    if "all" in affected or walk_issues:
        return False

    # _walk가 만든 필드만 재사용하여 AGE 중복과 자녀 월령 혼동을 막는다.
    fields = {item.get("field") for item in conditions}
    if "age" in fields:
        return False

    description = policy_node.get("eligibility")
    return isinstance(description, str) and _has_applicant_age_mention(
        description, has_child_condition="children" in fields,
    )


def check_eligibility(
    profile: dict,
    policy: dict,
) -> dict:
    """정규화된 프로필과 Hybrid 검색 정책 한 건을 비교한다."""

    if (
        not isinstance(profile, dict)
        or not isinstance(policy, dict)
    ):
        raise TypeError(
            "profile과 policy는 dict여야 합니다."
        )

    policy_id = policy.get("policy_id")

    if (
        not isinstance(policy_id, str)
        or not policy_id.strip()
    ):
        raise ValueError(
            "policy.policy_id는 비어 있지 않은 문자열이어야 합니다."
        )

    graph = (
        policy.get("graph")
        if isinstance(policy.get("graph"), dict)
        else {}
    )

    eligibility = (
        graph.get("eligibility")
        if isinstance(graph.get("eligibility"), dict)
        else {}
    )

    quality = (
        policy.get("data_quality")
        if isinstance(policy.get("data_quality"), dict)
        else {}
    )

    policy_node = (
        graph.get("policy")
        if isinstance(graph.get("policy"), dict)
        else {}
    )

    trees = eligibility.get("condition_tree")

    missing = (
        policy.get("graph_status") == "MISSING_IN_GRAPH"
        or not graph
    )

    conditions: list[dict] = []
    root_results: list[dict] = []
    walk_issues: set[str] = set()

    if (
        missing
        or not isinstance(trees, list)
        or not trees
    ):
        walk_issues.add("missing_tree")
        conditions = [
            _result(
                "eligibility",
                CHECK,
                "확인 가능한 자격 조건 구조가 없어 "
                "공식 공고 확인이 필요합니다.",
            )
        ]

    else:
        for tree in trees:
            _, details = _walk(tree, profile, issues=walk_issues)
            # _walk는 마지막에 루트 그룹의 종합 결과를 추가한다.
            # 참조를 그대로 저장하면 중첩 그룹을 다시 순회할 필요가 없다.
            root_results.append(details[-1])
            conditions.extend(details)

        if len(root_results) > 1:
            walk_issues.add("multiple_roots")
            conditions.append(
                _result(
                    "eligibility",
                    CHECK,
                    "여러 루트 조건 그룹의 결합 논리가 "
                    "확인되지 않았습니다.",
                )
            )

    affected = _affected_fields(
        quality,
        eligibility,
        missing,
    )

    for field in sorted(affected - {"all"}):
        if (
            field in {"age", "region", "income"}
            and not any(
                item["field"] == field
                for item in conditions
            )
        ):
            conditions.append(
                _result(
                    field,
                    CHECK,
                    "데이터 품질 경고로 해당 자격 요건의 "
                    "확인이 필요합니다.",
                )
            )

    if affected:
        for item in conditions:
            if (
                "all" in affected
                or item["field"] in affected
                or (
                    item["field"] == "eligibility"
                    and any(
                        value in affected
                        for value in (
                            "housing",
                            "employment",
                            "exclusion",
                            "marriage",
                            "children",
                        )
                    )
                )
            ):
                item["status"] = CHECK
                item["reason"] += (
                    " 데이터 품질 경고로 "
                    "자동 판정을 보류합니다."
                )

    if affected and "all" not in affected:
        for item in conditions:
            if item["field"] == "eligibility":
                item["status"] = CHECK

                if "데이터 품질 경고" not in item["reason"]:
                    item["reason"] += (
                        " 일부 조건이 검증되지 않아 "
                        "종합 판정을 보류합니다."
                    )

    review_items = _source_review_conditions(
        policy_node,
        profile,
    )
    if review_items:
        conditions.extend(review_items)

    # #42: 이미 수집한 조건 결과를 이용하므로 트리를 다시 순회하지 않는다.
    missing_age_notice = _should_add_missing_age_notice(
        policy_node, affected, conditions, walk_issues,
    )
    if missing_age_notice:
        conditions.append(
            _result(
                "age",
                CHECK,
                "이 정책의 나이 기준이 데이터에 정리되지 않아 "
                "공식 원문에서 확인이 필요합니다.",
            )
        )

    # 확인 항목은 정책 전체의 루트 종합 결과만 보류한다.
    # 하위 그룹/개별 조건과 명확한 ineligible 결과는 유지한다.
    if review_items or missing_age_notice:
        for item in root_results:
            if item["status"] == OK:
                item["status"] = CHECK
                if review_items:
                    item["reason"] += (
                        " 소득·가구 원문 확인이 남아 "
                        "종합 판정을 보류합니다."
                    )
                if missing_age_notice:
                    item["reason"] += (
                        " 나이 기준이 데이터에 정리되지 않아 "
                        "종합 판정을 보류합니다."
                    )

    return {
        "policy_id": policy_id,
        "conditions": conditions,
        "reception_status": _reception_status(policy),
    }


def summarize_conditions_for_display(conditions: list[dict]) -> list[dict]:
    """화면에 중복되는 소득·가구 안내를 합친다.

    원본 conditions를 변경하지 않고, 상태가 다른 항목 및 논리 그룹 결과는 유지한다.
    소득: field/label/reason이 같은 needs_check만 한 번 표시한다.
    가구: 원문 검토 안내와 기존 그래프 조건이 함께 있을 때 한 줄로 합치되,
          서로 다른 확인 사유도 생략하지 않는다.
    이 함수는 정책 한 건의 conditions에 대해서만 사용해야 한다.
    """
    if not isinstance(conditions, list):
        raise TypeError("conditions는 리스트여야 합니다.")
    if any(not isinstance(item, dict) for item in conditions):
        raise TypeError("각 condition은 dict여야 합니다.")

    def is_household_review(item: dict) -> bool:
        return (
            item.get("field") == "housing"
            and item.get("status") == CHECK
            and isinstance(item.get("reason"), str)
            and item["reason"].startswith("가구 요건의 공식 원문 기준을 추가 확인해야 합니다.")
        )

    # 가구 원문 검토 결과가 있을 때만, 같은 상태의 그래프 조건과 묶는다.
    # 중복 제거로 '무주택' 등의 개별 확인 사항을 잃지 않도록 reason을 합친다.
    housing_items = [
        item for item in conditions
        if item.get("field") == "housing" and item.get("status") == CHECK
    ]
    merge_housing = len(housing_items) > 1 and any(
        is_household_review(item) for item in housing_items
    )
    housing_summary: dict | None = None
    if merge_housing:
        # 그래프에서 나온 기존 조건을 우선 사용하고, 그 뒤에 원문 검토 안내를 결합한다.
        graph_items = [item for item in housing_items if not is_household_review(item)]
        review_items = [item for item in housing_items if is_household_review(item)]
        housing_summary = (graph_items or review_items)[0].copy()
        reasons = list(dict.fromkeys(
            str(item.get("reason") or "").strip()
            for item in [*graph_items, *review_items]
            if str(item.get("reason") or "").strip()
        ))
        housing_summary["reason"] = " / ".join(reasons)

    displayed: list[dict] = []
    income_notices: set[tuple[str, str, str]] = set()
    housing_emitted = False

    for item in conditions:
        if item.get("field") == "income" and item.get("status") == CHECK:
            key = (
                str(item.get("field")),
                str(item.get("label")),
                str(item.get("reason")),
            )
            if key in income_notices:
                continue
            income_notices.add(key)

        if merge_housing and item.get("field") == "housing" and item.get("status") == CHECK:
            if not housing_emitted:
                displayed.append(housing_summary)
                housing_emitted = True
            continue

        displayed.append(item.copy())

    return displayed
