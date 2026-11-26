"""Conservative grounding of inferred child-month ranges in model answers."""
from __future__ import annotations

import re


_CHILD_UPPER = re.compile(
    r"(?:자녀|아이|아기|영유아|영아|아동)(?:의)?\s*"
    r"(?:월령|연령|나이)(?:은|는|이|가)?\s*(?:생후\s*)?"
    r"(?P<limit>\d+)\s*개월\s*(?P<comparison>이하|미만)"
    # A comparator inside a negation or exclusion does not establish a bound.
    r"(?=(?:입니다|이다)?[ \t]*(?:[.。](?=\s|$)|\r?\n|$))"
)
_LOWER_EVIDENCE = re.compile(
    r"\d+\s*개월\s*(?:이상|초과|부터)|"
    r"\d+\s*(?:개월\s*)?(?:~|～|–|—|-)\s*\d+\s*개월|"
    r"출생|태어난|신생아"
)
_SENTENCE_BREAK = re.compile(r"(?<=[.!?。！？])(?:[ \t]+|(?=[가-힣]))|\r?\n")
_NEGATIVE_ELIGIBILITY = re.compile(
    r"제외|불가|비대상|대상(?:이|에)\s*(?:아니|아닙|해당하지\s*않)|"
    r"(?:신청|이용|지원받)(?:을)?\s*할\s*수\s*없"
)
_MARK = r"(?:\*\*|__)?"
_RANGE = re.compile(
    r"(?<![\d.\-])" + _MARK + r"(?P<low>\d+)\s*(?:개월)?" + _MARK
    + r"\s*(?:부터|이상\s*(?:이고|이며|,)?|~|～|–|—|-)\s*" + _MARK
    + r"(?P<high>\d+)\s*개월" + _MARK
    + r"(?:\s*" + _MARK + r"(?P<upper_word>까지|이하|미만)" + _MARK + r")?"
)
_START = re.compile(
    r"(?<![\d.\-])" + _MARK + r"(?:생후\s*)?(?P<low>\d+)\s*개월" + _MARK + r"\s*(?:이상\s*)?부터"
    r"(?=\s*(?:신청(?:할\s*수\s*있(?:습니다|어요)|\s*가능(?:합니다|해요))|"
    r"지원(?:을\s*받을\s*수\s*있(?:습니다|어요)|\s*가능(?:합니다|해요))|"
    r"이용(?:할\s*수\s*있(?:습니다|어요)|\s*가능(?:합니다|해요))|해당(?:합니다|됩니다)))"
)
_START_STATEMENT = re.compile(
    r"(?:시작|최소|최저)\s*월령(?:은|이|의\s*기준은)?\s*"
    + _MARK + r"(?P<low>\d+)\s*개월" + _MARK + r"\s*(?:입니다|이에요|로\s*확인됩니다)"
)


def _upper_only(contexts: list[dict]) -> tuple[str, str, int] | None:
    """Only an unambiguous, explicit month-age upper bound may be used.

    An explicit lower bound or birth condition anywhere in the same policy's
    supplied evidence prevents an 'unknown start' correction. Complex bounds
    and conflicting/alternative upper bounds are left for normal generation.
    """
    found: list[tuple[str, str, int]] = []
    for context in contexts:
        if context.get("quality_warnings"):
            return None
        text = re.sub(r"\*\*|__", "", context["text"])
        if _LOWER_EVIDENCE.search(text):
            return None
        # Any additional month expression could encode a lower bound, an
        # exception, or another measurement. Do not declare it absent merely
        # because the narrow canonical parser cannot interpret that wording.
        if "개월" in _CHILD_UPPER.sub("", text):
            return None
        for match in _CHILD_UPPER.finditer(text):
            before = _SENTENCE_BREAK.split(text[:match.start()])[-1]
            after = _SENTENCE_BREAK.split(text[match.end():], maxsplit=1)[0]
            if _NEGATIVE_ELIGIBILITY.search(before + match.group() + after):
                return None
            found.append((match["limit"], match["comparison"], context["source_id"]))
    if not found or len({(limit, comparison) for limit, comparison, _ in found}) != 1:
        return None
    return found[0]


def ground_child_month_ranges(response: dict, contexts: list[dict]) -> dict:
    """Remove an invented lower endpoint while preserving the grounded upper.

    Resolve citations per paragraph and then inspect all supplied evidence for
    that policy. Another policy's lower bound cannot justify this policy's
    range. Ambiguous multi-policy paragraphs are never rewritten by guessing.
    The input dictionaries are not mutated.
    """
    policies: dict[str, list[dict]] = {}
    source_policy = {}
    for context in contexts:
        policy = str(context.get("policy_id") or f"source:{context['source_id']}")
        policies.setdefault(policy, []).append(context)
        source_policy[context["source_id"]] = policy

    parts = re.split(r"(\n\s*\n)", response["answer"])
    added_sources = set()
    for index in range(0, len(parts), 2):
        paragraph = parts[index]
        if not re.search(r"자녀|아이|아기|영유아|영아|아동|월령", paragraph):
            continue
        if not re.search(r"신청|지원|이용|대상|원문|범위|기준", paragraph):
            continue
        # Do not rewrite a quote explaining why a proposed range is wrong.
        if re.search(r"(?:표현|문구).{0,15}(?:잘못|오류|쓰지|사용하지)", paragraph):
            continue
        cited_policies = {source_policy[int(m.group(1))]
                          for m in re.finditer(r"\[(\d{1,9})\]", paragraph)
                          if int(m.group(1)) in source_policy}
        if not cited_policies and len(policies) == 1:
            cited_policies = set(policies)
        if len(cited_policies) != 1:
            continue
        bound = _upper_only(policies[next(iter(cited_policies))])
        if bound is None:
            continue
        limit, comparison, source_id = bound
        changed = False

        def can_correct_start(match: re.Match[str]) -> bool:
            prefix = re.split(r"[.!?\n]", paragraph[:match.start()])[-1]
            suffix = paragraph[match.end():]
            # The paragraph may mention a child-age criterion and a different
            # measurement. A benefit/service duration is not an age range.
            if (
                re.search(r"기간|일정|납부|임대|휴직", prefix)
                or re.match(r"\s*(?:동안|간|에\s*걸쳐)", suffix)
            ):
                return False
            try:
                start, upper = int(match["low"]), int(limit)
            except ValueError:
                return False
            return start < upper or (start == upper and comparison == "이하")

        def replace(match: re.Match[str]) -> str:
            nonlocal changed
            if not can_correct_start(match):
                return match.group()
            # Do not manufacture a correction when even the upper endpoint
            # disagrees with the evidence; this guard handles inferred starts.
            if match["high"].lstrip("0") != limit.lstrip("0"):
                return match.group()
            answer_comparison = match["upper_word"]
            if answer_comparison in {"이하", "미만"} and answer_comparison != comparison:
                return match.group()
            changed = True
            return f"{limit}개월 {comparison}"

        paragraph = _RANGE.sub(replace, paragraph)

        def replace_start(match: re.Match[str]) -> str:
            nonlocal changed
            if not can_correct_start(match):
                return match.group()
            changed = True
            return f"{limit}개월 {comparison}인 경우"

        def replace_statement(match: re.Match[str]) -> str:
            nonlocal changed
            if not can_correct_start(match):
                return match.group()
            changed = True
            return "시작 월령은 이번 근거에서 확인되지 않았습니다"

        paragraph = _START.sub(replace_start, paragraph)
        paragraph = _START_STATEMENT.sub(replace_statement, paragraph)
        if changed:
            if "시작 월령은 이번 근거에서 확인되지 않았습니다" not in paragraph:
                paragraph += f" 시작 월령은 이번 근거에서 확인되지 않았습니다 [{source_id}]."
            elif f"[{source_id}]" not in paragraph:
                paragraph += f" [{source_id}]."
            parts[index] = paragraph
            added_sources.add(source_id)

    if not added_sources:
        return response
    return {**response, "answer": "".join(parts),
            "used_source_ids": sorted(set(response["used_source_ids"]) | added_sources)}
