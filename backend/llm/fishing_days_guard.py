"""Ground focused POL_GOV_006 fishing-day answers in the selected notice."""

import re


_POLICY_ID = "POL_GOV_006"
_NOTICE_DOCUMENT = "DOC_GOV_006_MOF_NOTICE_2024"
_SUMMARY_DOCUMENT = "DOC_GOV_006"
_FISHING_TOPIC = re.compile(r"조업\s*일수|며칠\s*(?:이상\s*)?조업|\d+\s*일\s*조업")
_OTHER_TOPICS = re.compile(r"신청\s*(?:방법|절차|기간|서류|주소|링크)|서류|교육|어업\s*허가|접수|지급액")
_CALENDAR_DATE = re.compile(r"\d{1,2}\s*월\s*\d{1,2}\s*일|\d{4}[-./]\d{1,2}[-./]\d{1,2}")
_DAY_RANGE = re.compile(r"\d+\s*(?:일\s*)?[~～–—-]\s*\d+\s*일")
_DAYS = re.compile(r"(?<![\d.,~～–—−-])(\d{1,3})\s*일(?![\d.]|\s*(?:이상|이하|초과|미만|[~～–—-]))")
_DAY_MENTION = re.compile(r"\d\s*일")
_TAC_STATUS = re.compile(
    r"(?:총허용어획량(?:\s*\(TAC\))?|TAC)(?:을)?\s*적용(?:하는)?\s*어종을\s*"
    r"주로\s*어획하는\s*어선(?:에\s*해당(?P<applies>합니다|해요|하고|하며|하므로|하지\s*않)|"
    r"(?P<is_vessel>입니다|이에요|이고|이므로|이\s*아닙니다|이\s*아니))"
)
_NOTICE_RULE = re.compile(
    r"최소\s*조업\s*일수\s*(?:[:：]\s*)?해당\s+연도에\s+(\d{1,3})\s*일\s*이상\s*조업할\s*것\.\s*"
    r"단,\s*총허용어획량을\s*적용하는\s*어종을\s*주로\s*어획하는\s*어선의\s*경우는\s*"
    r"(\d{1,3})\s*일\s*이상\s*조업하여도\s*인정"
)
_SUMMARY_LIMIT = re.compile(r"조업\s*일수(?:가|는)?\s*(\d{1,3})\s*일\s*이하")


def ground_fishing_days(response: dict, question: str, contexts: list[dict]) -> dict:
    """Keep the numerical comparison separate from a payout decision."""
    if (
        response["status"] != "answered"
        or not _FISHING_TOPIC.search(question)
        or _OTHER_TOPICS.search(question)
        or any(context.get("policy_id") != _POLICY_ID for context in contexts)
    ):
        return response

    count_text = _CALENDAR_DATE.sub("", question)
    values = _DAYS.findall(count_text)
    # Do not turn a quoted threshold, range, or multi-value question into a
    # comparison for one person's fishing days.
    if _DAY_RANGE.search(count_text) or len(values) > 1 or len(values) != len(_DAY_MENTION.findall(count_text)):
        return response

    rules = [
        (tuple(map(int, match.groups())), context["source_id"])
        for context in contexts if context.get("document_id") == _NOTICE_DOCUMENT
        for match in _NOTICE_RULE.finditer(context["text"])
    ]
    if len({rule for rule, _ in rules}) != 1:
        return response
    (minimum, tac_minimum), notice_id = rules[0]
    if not 0 < tac_minimum < minimum:
        return response

    rule = (f"해양수산부 고시의 최소 조업일수는 해당 연도 {minimum}일 이상입니다. "
            f"TAC 적용 어종을 주로 어획하는 어선은 {tac_minimum}일 이상이면 됩니다 [{notice_id}].")
    if not values:
        return {**response, "answer": rule, "used_source_ids": [notice_id]}

    days = int(values[0])
    tac_match = _TAC_STATUS.search(question)
    tac_phrase = (tac_match["applies"] or tac_match["is_vessel"]) if tac_match else ""
    if tac_minimum <= days < minimum and not tac_phrase:
        conclusion = f"{days}일은 일반 기준에 미달하지만, TAC 예외가 적용되면 최소 조업일수 요건을 충족합니다."
    else:
        uses_tac = tac_phrase and not re.search(r"않|아니|아닙", tac_phrase)
        minimum_for_input = tac_minimum if uses_tac else minimum
        verdict = "충족합니다" if days >= minimum_for_input else "충족하지 않습니다"
        conclusion = f"{days}일은 최소 조업일수 요건을 {verdict}."

    statements = [conclusion, rule]
    source_ids = {notice_id}
    summaries = [
        (int(match[1]), context["source_id"])
        for context in contexts if context.get("document_id") == _SUMMARY_DOCUMENT
        for match in _SUMMARY_LIMIT.finditer(context["text"])
    ]
    if len({limit for limit, _ in summaries}) == 1:
        limit, summary_id = summaries[0]
        membership = "포함됩니다" if days <= limit else "포함되지 않습니다"
        statements.append(f"정부24의 지급제한 안내에 적힌 ‘{limit}일 이하’에는 {days}일이 {membership} [{summary_id}].")
        source_ids.add(summary_id)
    statements.append("실제 지급제한과 최종 지급 자격은 별도 확인이 필요합니다.")
    return {**response, "answer": " ".join(statements), "used_source_ids": sorted(source_ids)}
