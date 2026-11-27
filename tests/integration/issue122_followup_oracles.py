"""#122 후속 판정 전달 검증용 독립 오라클. Checker·Pipeline 구현을 import하지 않는다.

- 종합 판정: 조건 배열의 마지막 field=eligibility 행(Checker·Pipeline·API 공통 관례, PR #148과 같은 기준).
- 답변 의미 분류: 사람이 읽는 종합 자격 안내가 '미확정'인지 '불충족'인지 보수적으로 구분한다.
  공식 원문의 조건 설명(예: "만 19~39세가 대상입니다")은 자동 자격 판정으로 보지 않는다.
"""

import re

CITATION = re.compile(r"\[(\d+)\]")

# 종합 자격을 미확정으로 안내하는 표현(확정 ineligible을 낮춰 말하는 경우를 잡는다).
UNDETERMINED = re.compile(
    r"(?:전체|종합|최종)?\s*(?:신청\s*|지원\s*)?자격(?:은|을|이)?\s*(?:아직\s*)?"
    r"(?:확정(?:할\s*수\s*없|하기\s*어렵|되지\s*않|되지\s*못)|판단(?:할\s*수\s*없|하기\s*어렵)|미확정)"
)
# 입력 정보로 확정된 불충족을 전하는 표현.
INELIGIBLE = re.compile(
    r"맞지\s*않는\s*조건|포함되지\s*않|해당하지\s*않|(?:신청|지원)\s*대상이\s*아니|(?:신청|지원)할\s*수\s*없"
)
# 지원 가능을 단정하는 표현.
ELIGIBLE = re.compile(
    r"자격(?:을|이)?\s*(?:모두\s*)?(?:충족|갖)(?:합니다|해요|하셨|하십니다|추셨|추고\s*있|췄)"
    r"|(?:신청|지원)\s*자격이\s*(?:있습|있어|있으|됩)"
)


def rows(conditions):
    """(field, status) 배열. 사유 문구 비교와 분리한다."""
    return [(str(row["field"]).lower(), row["status"]) for row in conditions]


def overall(conditions):
    """마지막 eligibility 행의 상태. 행이 없으면 None(종합 판정을 지어내지 않는다)."""
    for row in reversed(conditions):
        if str(row["field"]).lower() == "eligibility":
            return row["status"]
    return None


def answer_overall_claims(text):
    """답변이 담은 종합 자격 주장 종류 집합: undetermined / ineligible / eligible."""
    claims = set()
    if UNDETERMINED.search(text):
        claims.add("undetermined")
    if INELIGIBLE.search(text):
        claims.add("ineligible")
    if ELIGIBLE.search(text):
        claims.add("eligible")
    return claims


def semantic_mismatch(final_overall, text):
    """구조화된 종합 판정과 답변 의미가 어긋나면 사유 문자열, 맞으면 None.

    - ineligible인데 '자격 확정 불가'로 안내하거나 지원 가능을 단정 → 불일치.
    - needs_check인데 지원 가능을 단정 → 불일치(보수적 안내 위반).
    - eligible(개별 확인 조건 충족)은 전체 자격 보장이 아니므로 '미확정' 안내를 불일치로 보지 않는다.
    """
    claims = answer_overall_claims(text)
    if final_overall == "ineligible":
        if "undetermined" in claims:
            return "ineligible_presented_as_undetermined"
        if "eligible" in claims:
            return "ineligible_presented_as_eligible"
    if final_overall == "needs_check" and "eligible" in claims:
        return "needs_check_presented_as_eligible"
    return None
