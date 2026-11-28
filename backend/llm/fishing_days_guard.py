"""Ground focused POL_GOV_006 fishing-day answers in the selected notice.

#97(박소희)에서 추가한 POL_GOV_006 조업일수 답변 전용 보정이다.
기준 일수는 코드에 적지 않고 아래 문서의 원문에서 읽는다. 데이터 릴리스에서 이 정책·문서 ID가 바뀌면
보정이 조용히 꺼지므로 ID를 함께 갱신한다(tests/unit/test_issue97_fishing_days.py).
"""

import re


_POLICY_ID = "POL_GOV_006"
_NOTICE_DOCUMENT = "DOC_GOV_006_MOF_NOTICE_2024"
_SUMMARY_DOCUMENT = "DOC_GOV_006"
_CALENDAR_DATE = re.compile(r"\d{1,2}\s*월\s*\d{1,2}\s*일|\d{4}[-./]\d{1,2}[-./]\d{1,2}")
_DAY_RANGE = re.compile(r"\d+\s*(?:일\s*)?[~～–—-]\s*\d+\s*일")
_DAY_MENTION = re.compile(r"\d\s*일")
_COUNT_LABEL = r"조업\s*일수(?:가|는|이|은|[:：])?\s*(?:정확히\s*)?"
_COUNT_PHRASE = _COUNT_LABEL + r"\d{1,3}\s*일"
_STATED_DAYS = re.compile(
    r"(?<!\w)" + _COUNT_LABEL + r"(\d{1,3})\s*일"
    r"(?=\s*(?:입니다|이에요|이다(?!고)|이고|이며|이면|이라면|인데|인\s*경우|[.!?,。！？]|$))"
)
_NON_ACTUAL_PREFIX = re.compile(r"(?<!\w)(?:최소|최대|필요한|미|비)\s*$")
_WORKED_DAYS = re.compile(
    r"(?<![\d.,~～–—−-])(\d{1,3})\s*일(?:을)?\s*(?:동안\s*)?조업\s*"
    r"(?:했(?:습니다|어요|는데|고|으며|지만|다(?!고))|하였(?:습니다|고|으며))"
)
_TAC_STATUS = re.compile(
    r"(?:총허용어획량(?:\s*\(TAC\))?|TAC)(?:을)?\s*적용(?:하는)?\s*어종을\s*"
    r"주로\s*어획하는\s*어선(?:에\s*해당(?P<applies>합니다|해요|하고|하며|하므로|하지\s*않)|"
    r"(?P<is_vessel>입니다|이에요|이고|이므로|이\s*아닙니다|이\s*아니))"
)
_QUESTION_PREFIX = (
    r"(?:수산자원보호\s*직접지불제(?:에서|의|는)?\s+)?"
    r"(?:(?:해당\s*연도(?:에)?|올해|\d{4}\s*년(?:에)?|1년에|연간)\s+)?(?:기준으로\s+)?"
)
_MINIMUM_QUESTION = re.compile(
    _QUESTION_PREFIX
    + r"(?:(?:며칠|몇\s*일)\s*(?:이상\s*)?조업\s*(?:해야\s*(?:하나요|합니까|하죠)|하면\s*(?:되나요|됩니까))"
    r"|(?:최소\s*)?조업\s*일수(?:\s*(?:기준|요건))?(?:은|는|이|가|을|를)?\s*"
    r"(?:(?:며칠|몇\s*일)\s*(?:인가요|입니까|이에요|인가)|알려\s*주세요|어떻게\s*되나요))"
)
_QUALIFICATION_QUESTION = re.compile(
    _QUESTION_PREFIX + rf"(?:{_COUNT_PHRASE}(?:이면|이라면|인\s*경우)\s*[,，]?\s*)?"
    r"(?:(?:(?:최소\s*)?조업\s*일수(?:\s*(?:요건|조건|기준))?|(?:지급\s*)?요건|기준|조건)"
    r"(?:을|를|은|는)?\s*)?충족\s*(?:하나요|합니까|해요|하죠|하는지\s*알려\s*주세요)"
)
_DAY_STATEMENT = re.compile(_QUESTION_PREFIX + rf"(?:{_COUNT_PHRASE}(?:입니다|이에요|이다)|{_WORKED_DAYS.pattern})")
_TAC_STATEMENT = re.compile(
    _QUESTION_PREFIX + rf"(?:{_COUNT_PHRASE}(?:이고|이며)\s*[,，]?\s*)?{_TAC_STATUS.pattern}(?:습니다|아요|에요)?"
)
_DISTINCTION_REQUEST = re.compile(
    r"(?:원문의\s*)?(?:그\s*요건|(?:지급|최소\s*조업\s*일수)\s*요건)(?:과|와)\s*"
    r"지급\s*제한(?:\s*기준)?(?:\s*및\s*최종\s*지급\s*자격)?(?:을|를)\s*구분해서\s*알려\s*주세요"
)
_NOTICE_RULE = re.compile(
    r"최소\s*조업\s*일수\s*(?:[:：]\s*)?해당\s+연도에\s+(\d{1,3})\s*일\s*이상\s*조업할\s*것\.\s*"
    r"단,\s*총허용어획량을\s*적용하는\s*어종을\s*주로\s*어획하는\s*어선의\s*경우는\s*"
    r"(\d{1,3})\s*일\s*이상\s*조업하여도\s*인정"
)
_SUMMARY_LIMIT = re.compile(r"조업\s*일수(?:가|는)?\s*(\d{1,3})\s*일\s*이하")


def _asks_only_fishing_day_requirement(question: str, has_days: bool) -> bool:
    """Only replace replies when every part is covered by the generated answer."""
    asks_requirement = False
    for raw_part in re.split(r"(?<=[.!?。！？])\s+|\n+", question):
        part = raw_part.strip().rstrip(".!?。！？").rstrip()
        if _MINIMUM_QUESTION.fullmatch(part) or (has_days and _QUALIFICATION_QUESTION.fullmatch(part)):
            asks_requirement = True
        elif not has_days or not any(pattern.fullmatch(part) for pattern in (
            _DAY_STATEMENT, _TAC_STATEMENT, _DISTINCTION_REQUEST,
        )):
            return False
    return asks_requirement


def ground_fishing_days(response: dict, question: str, contexts: list[dict]) -> dict:
    """Keep the numerical comparison separate from a payout decision."""
    if (
        response["status"] != "answered"
        or any(context.get("policy_id") != _POLICY_ID for context in contexts)
    ):
        return response

    count_text = _CALENDAR_DATE.sub("", question).strip()
    values = [match[1] for match in _STATED_DAYS.finditer(count_text)
              if not _NON_ACTUAL_PREFIX.search(count_text[:match.start()])]
    values.extend(_WORKED_DAYS.findall(count_text))
    # A day count must be explicitly attached to a stated or completed amount
    # of fishing. Unmatched days, thresholds, and ranges keep the model reply.
    if _DAY_RANGE.search(count_text) or len(values) > 1 or len(values) != len(_DAY_MENTION.findall(count_text)):
        return response
    if not _asks_only_fishing_day_requirement(count_text, has_days=bool(values)):
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
