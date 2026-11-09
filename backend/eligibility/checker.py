
"""LifePolicy AI 자격 조건 판정기 (ask_contract v1.0).

입력은 Hybrid 검색 결과의 정책 객체 한 건이다.
DB/LLM 호출이나 입력 변형을 하지 않는다.
조건의 'eligible'은 개별/논리 조건의 충족일 뿐
정책 신청 자격 보증이 아니다.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

OK, NO, CHECK = "eligible", "ineligible", "needs_check"

_LABELS = {
    "age": "나이",
    "region": "거주 지역",
    "income": "소득",
    "eligibility": "자격 요건",
}

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

    return {
        "EMPLOYMENT": "employment",
        "EDUCATION": "education",
        "HOUSING_HOUSEHOLD": "housing",
        "SPECIAL_TARGET": "target",
    }.get(kind, "eligibility")


def _condition(condition: dict, profile: dict) -> dict:
    field = _field(condition)

    if field == "age":
        age = profile.get("age")

        if age is None:
            return _result(
                field,
                CHECK,
                "사용자의 만 나이가 입력되지 않았습니다.",
            )

        if (
            isinstance(age, bool)
            or not isinstance(age, int)
            or not 0 <= age <= 120
        ):
            return _result(
                field,
                CHECK,
                "유효한 만 나이를 확인할 수 없습니다.",
            )

        match = _AGE_RANGE.fullmatch(
            str(condition.get("value") or "")
        )

        if condition.get("operator") != "BETWEEN" or not match:
            return _result(
                field,
                CHECK,
                "연령 조건 형식을 해석할 수 없어 원문 확인이 필요합니다.",
            )

        low, high = map(int, match.groups())

        if not (0 <= low <= high <= 120):
            return _result(
                field,
                CHECK,
                "연령 범위가 유효하지 않아 확인이 필요합니다.",
            )

        status = OK if low <= age <= high else NO
        # v1.4.0에서는 상한값 120을 '상한 없음'의 표기로 사용한다.
        age_rule = (
            f"만 {low}세 이상(상한 없음)"
            if high == 120
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
        # 지역 데이터의 신뢰성이 확보되지 않았으므로
        # 사용자의 주소와 정책의 지역을 자동 비교하지 않는다.
        return _result(
            field,
            CHECK,
            "지역 범위·거주 요건의 공식 근거 확인이 필요합니다.",
        )

    if field == "income":
        # 개인/가구 소득 구분 및 중위소득 기준이 불명확하다.
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
                "조건 그룹이 누락·순환되었거나 깊이 제한을 초과했습니다.",
            )
        ]

    logic = str(group.get("logic") or "UNKNOWN").upper()

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

    if not isinstance(conditions, list) or not isinstance(children, list):
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

    # NOT 및 OR 집합의 의미는 개별 항목으로 표현할 수 없으므로
    # 그룹별 결론을 함께 남긴다.
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

    # 품질 경고의 속성명은 출력 필드명으로 정규화한다.
    # 인식하지 못하는 속성이 있으면 해당 경고를 무시하지 않고
    # 전체 조건의 자동 판정을 보류한다 (fail closed).
    aliases = {
        "residence": "region",
        "location": "region",
        "housing_household": "housing",
        "household": "housing",
        "special_target": "target",
    }
    supported = {
        "all",
        "age",
        "region",
        "income",
        "eligibility",
        "employment",
        "education",
        "housing",
        "target",
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


def _reception_status(policy: dict) -> str:
    """검증된 ISO 날짜를 가진 별도 명시 구조만 분류한다.

    실제 graph.policy.application_period는 자유 텍스트이며
    여기서는 사용하지 않는다.
    """

    proof = policy.get("verified_reception_period")

    if (
        not isinstance(proof, dict)
        or proof.get("official_source_checked") is not True
    ):
        return "unknown"

    start = proof.get("start_date")
    end = proof.get("end_date")

    if not isinstance(start, str) or not isinstance(end, str):
        return "unknown"

    try:
        begin = date.fromisoformat(start)
        finish = date.fromisoformat(end)
    except ValueError:
        return "unknown"

    if begin > finish:
        return "unknown"

    today = datetime.now(
        ZoneInfo("Asia/Seoul")
    ).date()

    if today < begin:
        return "before"

    if today > finish:
        return "closed"

    return "open"


def check_eligibility(
    profile: dict,
    policy: dict,
) -> dict:
    """정규화된 프로필과 Hybrid 검색 정책 한 건을 비교한다."""

    if not isinstance(profile, dict) or not isinstance(policy, dict):
        raise TypeError(
            "profile과 policy는 dict여야 합니다."
        )

    policy_id = policy.get("policy_id")

    if not isinstance(policy_id, str) or not policy_id.strip():
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

    trees = eligibility.get("condition_tree")

    missing = (
        policy.get("graph_status") == "MISSING_IN_GRAPH"
        or not graph
    )

    conditions: list[dict] = []

    if missing or not isinstance(trees, list) or not trees:
        conditions = [
            _result(
                "eligibility",
                CHECK,
                "확인 가능한 자격 조건 구조가 없어 공식 공고 확인이 필요합니다.",
            )
        ]

    else:
        roots = []

        for tree in trees:
            state, details = _walk(tree, profile)

            roots.append(state)
            conditions.extend(details)

        # 루트 그룹 여러 개의 결합 관계는 명세되지 않았으므로
        # 임의로 AND 처리하지 않는다.
        if len(roots) > 1:
            conditions.append(
                _result(
                    "eligibility",
                    CHECK,
                    "여러 루트 조건 그룹의 결합 논리가 확인되지 않았습니다.",
                )
            )

    affected = _affected_fields(
        quality,
        eligibility,
        missing,
    )

    # 그래프에서 누락된 영향을 받은 조건도 표시한다.
    for field in sorted(affected - {"all"}):
        if field in {"age", "region", "income"} and not any(
            item["field"] == field
            for item in conditions
        ):
            conditions.append(
                _result(
                    field,
                    CHECK,
                    "데이터 품질 경고로 해당 자격 요건의 확인이 필요합니다.",
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
                    " 데이터 품질 경고로 자동 판정을 보류합니다."
                )

    # 품질 경고가 있는 속성의 조건 노드가 아예 누락될 수도 있다.
    # 다른 조건이 eligible이어도 그룹의 종합 판정은 보류한다.
    if affected and "all" not in affected:
        for item in conditions:
            if item["field"] == "eligibility":
                item["status"] = CHECK

                if "데이터 품질 경고" not in item["reason"]:
                    item["reason"] += (
                        " 일부 조건이 검증되지 않아 "
                        "종합 판정을 보류합니다."
                    )

    return {
        "policy_id": policy_id,
        "conditions": conditions,
        "reception_status": _reception_status(policy),
    }
