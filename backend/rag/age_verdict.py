"""생성 답변의 '입력한 나이가 기준에 맞는다/안 맞는다' 결론을 Checker 나이 판정에 맞춘다(#122·#90·#78 후속).

실제 LLM이 같은 근거 안의 다른 연령 표기(예: 정부24 구조화 요약 18~39세)나 '국민 누구나' 같은 문구를 따라
Checker 판정과 반대로 안내한 사례가 있었다(dev 0cb19f6 인천 18세, POL_005 76세 직접 질문).

판정 방향이 확실한 정책만 다룬다.
- 나이 조건이 하나이고 OR·NOT 그룹이 없으면 그 나이 조건은 신청 요건이다. Checker 사유의 '포함됩니다/포함되지 않습니다'가
  곧 나이 요건 충족 여부다.
- 나이 조건이 NOT 아래에 있으면(제외 기준) Checker가 확정해 전달한 나이 제외 사유(exclusion_reasons)가 있을 때만
  '제외 대상'으로 본다. 확정 사유가 없으면 방향을 알 수 없으므로 건드리지 않는다.
나이를 입력하지 않았거나, 나이 조건이 여러 개이거나, 사유 형식이 다르면 아무것도 바꾸지 않는다.
답변에서는 나이에 대한 결론을 단정한 절만 고치고, 유보 절과 다른 조건·원문 연령 표기 설명은 그대로 둔다.
"""

import re

from backend.core.contract import CITATION, SENTENCE_BREAK

# 쉼표(숫자 사이 제외)와 '다만·하지만·그러나', '~지만' 뒤에서 절을 나눈다.
CLAUSE_BREAK = re.compile(r"((?<!\d),\s+|\s+(?=(?:다만|하지만|그러나)\s)|(?<=지만)\s+)")
AGE_REASON = re.compile(r"^(만 (\d+)~(\d+)세|만 (\d+)세 이상\(상한 없음\)) 기준에 입력한 만 (\d+)세가 (포함됩니다|포함되지 않습니다)\.")
GROUP_REASON = {"OR": "조건 중 하나 이상 충족해야 합니다(종합 결과).", "NOT": "해당 조건이 충족되지 않아야 합니다(종합 결과)."}
LEAD = re.compile(r"^\s*((?:\[\d+\]\s*)*)((?:다만|또한|그리고|하지만|그러나|정리하면|즉)\s*,?\s*)?")
HEDGE = re.compile(r"여부|인지|확인이 필요|확인해야|확인해 주세요|확정할 수 없|확정하기 어렵|단정|알 수 없|모르")
# 나이 결론의 방향. '제외되지 않'은 긍정이고, 그 밖의 '제외'와 부정형은 부정이다.
POSITIVE_EXCEPTION = re.compile(r"제외되지\s*않|제외\s*대상이\s*아니|제외\s*사유에\s*해당하지\s*않")
NEGATIVE = re.compile(
    r"포함되지\s*않|포함하지\s*않|해당하지\s*않|해당되지\s*않|충족하지\s*(?:않|못)|충족되지\s*않|맞지\s*않|들지\s*않"
    r"|벗어나|미포함|제외|볼\s*수\s*없|아닙니다|아니에요"
)
# '포함·해당'은 기준이 제외 기준이면 '제외 대상'이라는 뜻이 된다. '충족·통과' 등은 문맥과 관계없이 긍정이다.
MEMBERSHIP = re.compile(r"포함|해당|들어가|들어갑|들어와|범위\s*안")
POSITIVE = re.compile(r"충족|맞습|맞아|맞는|부합|통과")
AGE_TOPIC = re.compile(r"나이|연령")


def _age_verdicts(checks: list[dict], age: int) -> dict[str, dict]:
    """정책별 나이 판정. {'ok': 나이로 신청 가능한지, 'rule': 기준 문구, 'exclusion': 제외 기준인지}."""
    verdicts = {}
    for check in checks:
        rows = check.get("conditions") or []
        ages = [row for row in rows if str(row.get("field", "")).lower() == "age"]
        if len(ages) != 1:
            continue
        match = AGE_REASON.match(str(ages[0].get("reason") or ""))
        if not match or int(match.group(5)) != age:
            continue
        rule, included = match.group(1), match.group(6) == "포함됩니다"
        reasons = {str(row.get("reason") or "") for row in rows}
        has_not = any(r.startswith(GROUP_REASON["NOT"]) for r in reasons)
        has_or = any(r.startswith(GROUP_REASON["OR"]) for r in reasons)
        excluded = any(str(r.get("field", "")).lower() == "age" for r in (check.get("exclusion_reasons") or []))
        if excluded and included:
            verdicts[check["policy_id"]] = {"ok": False, "rule": rule, "exclusion": True}
        elif not has_not and not has_or:
            verdicts[check["policy_id"]] = {"ok": included, "rule": rule, "exclusion": False}
    return verdicts


def _about_user(plain: str, age: int) -> bool:
    """절이 기준 범위 설명이 아니라 '입력한 나이'의 결론을 말하는지.

    '만 18세는/도/인', '입력·사용자·본인', '나이만으로·나이 기준만' 같은 표현이 있어야 한다.
    '18세~39세', '18세 이상'처럼 기준 범위 안의 숫자는 사용자 나이로 보지 않는다.
    """
    if re.search(r"입력|사용자|본인|나이만|나이\s*기준만|나이만\s*보면|연령만", plain):
        return True
    for match in re.finditer(rf"(?<![\d~∼\-])(?:만\s*)?{age}\s*세(?!\s*[~∼\-]|\s*(?:이상|이하|미만|초과|부터|까지))", plain):
        if re.match(r"\s*(?:는|도|이|가|인|라면|이라|이므로|이면|이시|이신)", plain[match.end():]):
            return True
    return False


def _claim(plain: str, age: int) -> bool | str | None:
    """절이 입력 나이에 대해 단정한 결론. True=나이로는 가능, False=불가, None=단정 아님.

    'member'는 '기준에 포함/해당'만 말한 경우다. 그 기준이 신청 요건인지 제외 기준인지에 따라 뜻이 갈리므로
    대상 정책의 판정을 안 뒤에 정한다(align_age_verdicts).
    """
    if not (AGE_TOPIC.search(plain) or re.search(rf"{age}\s*세", plain)) or not _about_user(plain, age):
        return None
    if HEDGE.search(plain):
        return None
    if POSITIVE_EXCEPTION.search(plain):
        return True
    if NEGATIVE.search(plain):
        return False
    if POSITIVE.search(plain):
        return True
    if MEMBERSHIP.search(plain):
        return "member"
    return None


def _statement(verdict: dict, age: int, last: bool) -> str:
    """Checker 판정을 그대로 옮긴 문장. 문장 끝이 아니면 다음 절과 이어지도록 연결형으로 쓴다."""
    if verdict["exclusion"]:
        stem = f"입력하신 만 {age}세는 지원 제외 기준({verdict['rule']})에 해당"
        return stem + ("돼요." if last else "하고")
    # 나이 조건이 검수 대기(데이터 품질 보류)일 수 있으므로 '확인된 기준'이라고 단정하지 않는다.
    stem = f"입력하신 만 {age}세는 연령 기준({verdict['rule']})에 포함"
    if verdict["ok"]:
        return stem + ("돼요." if last else "되고")
    return stem + ("되지 않아요." if last else "되지 않고")


def _target(plain: str, numbers: list[str], policy_of: dict, names: dict, verdicts: dict, single: bool) -> str | None:
    """절이 가리키는 정책: 절의 정책명 → 인용 → (판정 정책이 하나뿐인) 답변 전체 순서로 하나로 특정될 때만."""
    cited = {policy_of.get(n) for n in numbers} - {None}
    named = {pid for pid, name in names.items() if name and name in plain}
    targets = named or cited or (set(verdicts) if single else set())
    return next(iter(targets)) if len(targets) == 1 else None


def _rewrite(clause: str, text: str, is_last: bool) -> str:
    """절의 머리(인용·'다만' 등)와 인용 번호는 남기고 내용만 판정 문장으로 바꾼다."""
    lead = LEAD.match(clause)
    prefix = (lead.group(1) or "") + (lead.group(2) or "")
    markers = "".join(f"[{n}]" for n in dict.fromkeys(CITATION.findall(clause)))
    tail = re.search(r"\s*$", clause).group()
    return f"{prefix}{text}{' ' + markers if markers else ''}" + (tail if not is_last else "")


def align_age_verdicts(answer: str, contexts: list[dict], checks: list[dict], age: int | None) -> tuple[str, bool]:
    """Checker 나이 판정과 반대인 나이 결론 절만 판정 문장으로 바꾼다. (고친 답변, 변경 여부)"""
    if age is None or isinstance(age, bool):
        return answer, False
    verdicts = _age_verdicts(checks, age)
    if not verdicts:
        return answer, False
    policy_of = {str(ctx["source_id"]): ctx["policy_id"] for ctx in contexts}
    names = {check["policy_id"]: check.get("policy_name") or "" for check in checks}
    parts = SENTENCE_BREAK.split(answer)
    changed = False
    for index in range(0, len(parts), 2):
        original = parts[index]
        if not original.strip():
            continue
        # Markdown 강조가 절 경계('~지만**')에 걸리면 나눌 수 없으므로 이 문장에서는 강조를 빼고 본다.
        # 실제로 바꾼 문장만 강조가 빠지고, 바꾸지 않은 문장은 원문 그대로 둔다.
        sentence = original.replace("**", "")
        clauses = CLAUSE_BREAK.split(sentence)
        sentence_numbers = CITATION.findall(sentence)
        mentions_exclusion = "제외" in sentence
        last = len(clauses) - 1
        sentence_changed = False
        for position in range(0, len(clauses), 2):
            clause = clauses[position]
            plain = CITATION.sub("", clause).replace("`", "")
            claim = _claim(plain, age)
            if claim is None:
                continue
            pid = _target(plain, CITATION.findall(clause) or sentence_numbers, policy_of, names, verdicts,
                          len(checks) == 1)
            verdict = verdicts.get(pid) if pid else None
            if verdict is None:
                continue
            if claim == "member":
                # 제외 기준 정책에서 같은 문장이 제외 기준을 말하면 '기준에 포함'은 제외 대상이라는 뜻이다.
                claim = not (verdict["exclusion"] and mentions_exclusion)
            if claim == verdict["ok"]:
                continue
            is_last = position == last
            text = _statement(verdict, age, is_last)
            if len(checks) > 1:
                text = f"{names[pid]}: {text}"
            clauses[position] = _rewrite(clause, text, is_last)
            sentence_changed = changed = True
        if sentence_changed:
            parts[index] = "".join(clauses)
    return "".join(parts), changed


# ---- 자녀 월령(#39) ----
# 원문에 '24개월 미만 아이'와 '생후 24개월까지 지원'이 함께 있어 LLM이 24개월을 '제외'로 답한 사례가 있었다
# (dev b5617b2 POL_GOV_050 24개월, Checker는 #39 합의대로 LTE 24 '포함'). 나이와 같은 기준으로 고친다.
CHILD_REASON = re.compile(r"^자녀 월령 (\d+)개월 (이하|미만) 기준에 입력한 (\d+)개월은 (포함됩니다|포함되지 않습니다)\.")
MONTH_TOPIC = re.compile(r"월령|개월")
# 앞 절의 월령 결론을 이어 받기만 하는 절('이 기준만 보면 대상에서 제외됩니다').
CONTINUATION = re.compile(r"^\s*(?:(?:이|그)\s*기준(?:만|으로|에서|상)|따라서|그래서|이\s*경우)")


def _month_verdicts(checks: list[dict], months: int) -> dict[str, dict]:
    """정책별 자녀 월령 판정. 월령 조건이 하나이고 OR·NOT 그룹이 없는 정책만 다룬다."""
    verdicts = {}
    for check in checks:
        rows = check.get("conditions") or []
        children = [row for row in rows if str(row.get("field", "")).lower() == "children"]
        if len(children) != 1:
            continue
        match = CHILD_REASON.match(str(children[0].get("reason") or ""))
        if not match or int(match.group(3)) != months:
            continue
        reasons = {str(row.get("reason") or "") for row in rows}
        if any(r.startswith(GROUP_REASON["NOT"]) or r.startswith(GROUP_REASON["OR"]) for r in reasons):
            continue
        verdicts[check["policy_id"]] = {"ok": match.group(4) == "포함됩니다",
                                        "rule": f"{match.group(1)}개월 {match.group(2)}"}
    return verdicts


def _month_claim(plain: str, months: int) -> bool | None:
    """절이 입력한 자녀 월령에 대해 단정한 결론. True=월령으로는 가능, False=불가, None=단정 아님.

    '자녀 월령 24개월은', '입력한 24개월' 같은 표현이 있어야 한다. '24개월 미만 아이'처럼 기준 숫자는 보지 않는다.
    """
    if not MONTH_TOPIC.search(plain):
        return None
    if not (re.search(r"입력", plain)
            or re.search(rf"(?<![\d~∼\-]){months}\s*개월\s*(?:은|는|이|가|이라|이므로|이면|인\s)", plain)):
        return None
    if HEDGE.search(plain):
        return None
    if POSITIVE_EXCEPTION.search(plain):
        return True
    if NEGATIVE.search(plain):
        return False
    if POSITIVE.search(plain) or MEMBERSHIP.search(plain):
        return True
    return None


def align_child_month_verdicts(answer: str, contexts: list[dict], checks: list[dict],
                               months: int | None) -> tuple[str, bool]:
    """Checker 자녀 월령 판정과 반대인 월령 결론 절을 판정 문장으로 바꾼다. (고친 답변, 변경 여부)

    바꾼 절 바로 뒤에서 그 결론만 이어 받는 절('이 기준만 보면 대상에서 제외됩니다')도 같은 결론이므로 뺀다.
    """
    if months is None or isinstance(months, bool):
        return answer, False
    verdicts = _month_verdicts(checks, months)
    if not verdicts:
        return answer, False
    policy_of = {str(ctx["source_id"]): ctx["policy_id"] for ctx in contexts}
    names = {check["policy_id"]: check.get("policy_name") or "" for check in checks}
    parts = SENTENCE_BREAK.split(answer)
    changed = False
    for index in range(0, len(parts), 2):
        original = parts[index]
        if not MONTH_TOPIC.search(original):
            continue
        clauses = CLAUSE_BREAK.split(original.replace("**", ""))
        sentence_numbers = CITATION.findall(original)
        last = len(clauses) - 1
        replaced = None  # 바로 앞에서 바꾼 절 위치와 판정
        sentence_changed = False
        for position in range(0, len(clauses), 2):
            clause = clauses[position]
            plain = CITATION.sub("", clause).replace("`", "")
            if replaced is not None and CONTINUATION.match(plain):
                at, verdict, pid = replaced
                follow = False if NEGATIVE.search(plain) else (True if POSITIVE.search(plain) else None)
                if follow is not None and follow != verdict["ok"]:
                    # 이어 받은 절을 빼고, 앞에서 바꾼 절을 문장 끝 형태로 다시 쓴다(인용은 모두 남긴다).
                    markers = "".join(f"[{n}]" for n in dict.fromkeys(
                        CITATION.findall(clauses[at]) + CITATION.findall(clause)))
                    lead = LEAD.match(clauses[at])
                    prefix = (lead.group(1) or "") + (lead.group(2) or "")
                    text = _month_statement(verdict, months, True, names[pid] if len(checks) > 1 else "")
                    clauses[at] = f"{prefix}{text}{' ' + markers if markers else ''}"
                    for gone in range(at + 1, position + 1):
                        clauses[gone] = ""
                    replaced = None
                    continue
            replaced = None
            claim = _month_claim(plain, months)
            if claim is None:
                continue
            pid = _target(plain, CITATION.findall(clause) or sentence_numbers, policy_of, names, verdicts,
                          len(checks) == 1)
            verdict = verdicts.get(pid) if pid else None
            if verdict is None or claim == verdict["ok"]:
                continue
            is_last = position == last
            text = _month_statement(verdict, months, is_last, names[pid] if len(checks) > 1 else "")
            clauses[position] = _rewrite(clause, text, is_last)
            replaced = (position, verdict, pid)
            sentence_changed = changed = True
        if sentence_changed:
            parts[index] = "".join(clauses)
    return "".join(parts), changed


def _month_statement(verdict: dict, months: int, last: bool, name: str = "") -> str:
    """Checker 자녀 월령 판정을 그대로 옮긴 문장. 문장 끝이 아니면 연결형으로 쓴다."""
    stem = f"{name + ': ' if name else ''}입력하신 자녀 월령 {months}개월은 월령 기준({verdict['rule']})에 포함"
    if verdict["ok"]:
        return stem + ("돼요." if last else "되고")
    return stem + ("되지 않아요." if last else "되지 않고")
