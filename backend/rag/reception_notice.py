"""접수 상태가 unknown인 정책의 합의 문구(#103·#147)를 생성 답변에 보장한다.

프롬프트로 문구를 지정해도 실제 LLM이 문구를 빼거나("접수 여부도 확인되지 않았습니다"처럼) 바꿔 쓰는 경우가 있다.
이 모듈은 답변이 인용한 정책 중 Checker 접수 상태가 unknown인 정책에 대해서만,
1) 접수 여부를 '확인 불가'로 바꿔 쓴 짧은 문장을 합의 문구로 바꾸고,
2) 그래도 합의 문구가 없으면 답변 끝에 합의 문구를 덧붙인다.
접수 중·마감·예정을 단정한 문장은 기존 Checker 정렬(_align_answer_with_checks)이 처리하므로 여기서 다루지 않는다.
"""

import re

CITATION = re.compile(r"\[(\d+)\]")
UNKNOWN_RECEPTION = "현재 접수 여부는 공식 공고에서 확인해 주세요."
PHRASE = UNKNOWN_RECEPTION.rstrip(".")
SENTENCE_BREAK = re.compile(r"((?<=[.!?。！？])\s+|\n+)")
PARAGRAPH_BREAK = re.compile(r"\r?\n[ \t]*\r?\n")
# 접수 여부를 '확인할 수 없다'는 뜻으로 바꿔 쓴 표현. 접수 시점·일정 설명은 대상이 아니다.
PARAPHRASE = re.compile(
    r"접수\s*(?:여부|상태|가능\s*여부)\S{0,2}\s*.{0,30}?"
    r"(?:확인되지\s*않|확인할\s*수\s*없|확인이\s*어렵|확인하기\s*어렵|알\s*수\s*없|불분명|불확실"
    r"|명시되어\s*있지\s*않|나와\s*있지\s*않|적혀\s*있지\s*않)"
)
# 조사만 바꾼 합의 문구('접수 여부도 공식 공고에서 확인해 주세요')는 그 자리에서 합의 문구로 맞춘다.
NEAR_PHRASE = re.compile(r"(?:현재\s*)?접수\s*여부(?:도|는)?\s*공식\s*공고에서\s*확인해\s*주세요")
# 이보다 긴 문장은 다른 설명이 섞였을 수 있으므로 통째로 바꾸지 않고, 끝에 합의 문구만 덧붙인다.
MAX_REWRITE_LENGTH = 60


def _cited_unknown(answer: str, policy_of: dict[str, str], by_id: dict[str, dict]) -> list[str]:
    """답변이 인용한 정책 중 접수 상태가 unknown인 정책 ID(인용 순서)."""
    cited = [policy_of[n] for n in dict.fromkeys(CITATION.findall(answer)) if n in policy_of]
    return [pid for pid in dict.fromkeys(cited)
            if pid in by_id and by_id[pid].get("reception_status") == "unknown"]


def ensure_unknown_reception_notice(answer: str, contexts: list[dict], checks: list[dict]) -> tuple[str, bool]:
    """unknown 정책의 합의 문구가 빠졌거나 바꿔 쓴 경우만 고친다. (고친 답변, 변경 여부)"""
    policy_of = {str(ctx["source_id"]): ctx["policy_id"] for ctx in contexts}
    by_id = {check["policy_id"]: check for check in checks}
    targets = _cited_unknown(answer, policy_of, by_id)
    if not targets:
        return answer, False
    normalized = NEAR_PHRASE.sub(PHRASE, answer)
    changed = normalized != answer
    parts = SENTENCE_BREAK.split(normalized)
    for index in range(0, len(parts), 2):
        sentence = parts[index]
        if PHRASE in sentence or not PARAPHRASE.search(sentence):
            continue
        numbers = list(dict.fromkeys(CITATION.findall(sentence)))
        cited = {policy_of.get(n) for n in numbers}
        # 인용한 정책이 모두 unknown일 때만 바꾼다. 인용이 없으면 답변의 대상이 unknown 정책 하나일 때만 바꾼다.
        if numbers and not cited <= set(targets):
            continue
        if not numbers and len(targets) != 1:
            continue
        if len(CITATION.sub("", sentence).strip()) > MAX_REWRITE_LENGTH:
            continue
        markers = "".join(f"[{n}]" for n in numbers)
        parts[index] = f"{UNKNOWN_RECEPTION} {markers}".rstrip()
        changed = True
    fixed = "".join(parts)
    sentences = [s for s in SENTENCE_BREAK.split(fixed)[::2] if PHRASE in s]
    if len(targets) == 1:
        missing = [] if sentences else targets
    else:
        # 여러 unknown 정책이면 합의 문구와 같은 문단에 정책명이나 자기 인용이 있는 정책만 안내된 것으로 본다.
        # 문구 뒤에 붙은 인용('…확인해 주세요. [1]')은 문장 분리에서 떨어지므로 문단 단위로 본다.
        paragraphs = [p for p in PARAGRAPH_BREAK.split(fixed) if PHRASE in p]

        def covered(pid: str) -> bool:
            own = {n for n, owner in policy_of.items() if owner == pid}
            name = by_id[pid].get("policy_name") or ""
            return any((name and name in p) or own & set(CITATION.findall(p)) for p in paragraphs)
        missing = [pid for pid in targets if not covered(pid)]
    if missing:
        cited = {policy_of[n] for n in CITATION.findall(fixed) if n in policy_of}
        # 답변이 한 정책만 인용했을 때만 정책명 없이 덧붙인다. 다른 정책도 인용했다면 바로 앞 문단의
        # 다른 정책 안내로 읽히지 않도록 정책명을 붙인다.
        if len(cited) == 1:
            notes = UNKNOWN_RECEPTION
        else:
            notes = " ".join(f"{by_id[pid]['policy_name']}: {UNKNOWN_RECEPTION}" for pid in missing)
        fixed = f"{fixed.rstrip()}\n\n{notes}"
        changed = True
    return fixed, changed
