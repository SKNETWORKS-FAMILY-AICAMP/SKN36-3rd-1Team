
"""LifePolicy AI 자격 조건 판정기 (ask_contract v1.0).

입력은 Hybrid 검색 결과의 정책 객체 한 건이다.
DB/LLM 호출이나 입력 변형을 하지 않는다.
조건의 'eligible'은 개별/논리 조건의 충족일 뿐
정책 신청 자격 보증이 아니다.
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime, timedelta, timezone

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
) -> tuple[str, list[dict]]:

    if (
        depth > 10
        or not isinstance(group, dict)
        or group.get("cycle")
        or group.get("not_loaded")
    ):
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
        return CHECK, [
            _result(
                "eligibility",
                CHECK,
                "조건 그룹의 내부 구조가 올바르지 않습니다.",
            )
        ]

    for condition in conditions:
        if not isinstance(condition, dict):
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
        )

        states.append(status)
        items.extend(nested)

    group_status = _combine(logic, states)

    items.append(
        _result(
            "eligibility",
            group_status,
            f"조건 그룹 {group.get('group_id') or 'ID 없음'} "
            f"({logic})의 종합 평가 결과입니다.",
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

    supported = {
        "all",
        "age",
        "region",
        "income",
        "housing",
        "employment",
        "exclusion",
        "marriage",
        "children",
    }

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

    if (
        missing
        or not isinstance(trees, list)
        or not trees
    ):
        conditions = [
            _result(
                "eligibility",
                CHECK,
                "확인 가능한 자격 조건 구조가 없어 "
                "공식 공고 확인이 필요합니다.",
            )
        ]

    else:
        roots = []

        for tree in trees:
            state, details = _walk(tree, profile)
            roots.append(state)
            conditions.extend(details)

        if len(roots) > 1:
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
                            "target",
                            "education",
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

        for item in conditions:
            if (
                item["field"] == "eligibility"
                and item["status"] == OK
            ):
                item["status"] = CHECK
                item["reason"] += (
                    " 소득·가구 원문 확인이 남아 "
                    "종합 판정을 보류합니다."
                )

    return {
        "policy_id": policy_id,
        "conditions": conditions,
        "reception_status": _reception_status(policy),
    }


def summarize_conditions_for_display(
    conditions: list[dict],
) -> list[dict]:
    """동일한 소득 확인 안내만 화면용으로 합친다.

    원본 개별 조건·논리 조건·상태는 변경하지 않는다.
    """

    if not isinstance(conditions, list):
        raise TypeError(
            "conditions는 리스트여야 합니다."
        )

    displayed: list[dict] = []
    income_notices: set[
        tuple[str, str, str]
    ] = set()

    for item in conditions:
        if not isinstance(item, dict):
            raise TypeError(
                "각 condition은 dict여야 합니다."
            )

        if (
            item.get("field") == "income"
            and item.get("status") == CHECK
        ):
            key = (
                str(item.get("field")),
                str(item.get("label")),
                str(item.get("reason")),
            )

            if key in income_notices:
                continue

            income_notices.add(key)

        displayed.append(item.copy())

    return displayed
