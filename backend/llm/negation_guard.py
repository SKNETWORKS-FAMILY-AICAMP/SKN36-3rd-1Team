"""답변이 원문의 제외 대상에서 부정 접두어(미·비·무·불)를 빠뜨려 뜻을 뒤집은 경우만 원문 표현으로 되돌린다.

실제 LLM이 서울시 청년수당의 신청제외대상 "주민등록상 서울시 미거주자"를 "서울시 거주자 제외"로 옮긴 사례가 있다.
이 모듈은 아래 조건을 모두 만족할 때만 그 단어 앞에 접두어를 되붙인다. 하나라도 불확실하면 고치지 않는다.

1. 답변 문장이 출처 번호 [n]을 달았고, 그 문장에서 단어 바로 뒤에 '제외'가 온다("거주자 제외", "거주자는 제외").
2. 인용한 원문에 접두어가 붙은 같은 단어가 제외 문맥에 있다("신청제외대상 … 미거주자").
3. 인용한 원문 어디에도 접두어 없는 그 단어가 없다(있으면 어느 쪽이 맞는지 알 수 없다).

신청 대상을 설명하는 문장("서울시 거주자만 신청할 수 있어요")이나 이미 원문대로 쓴 문장은 건드리지 않는다.
입력 dict는 바꾸지 않는다.
"""

import logging
import re

from backend.core.contract import CITATION, SENTENCE_BREAK

logger = logging.getLogger(__name__)

# 뜻을 뒤집는 접두어. '미거주자'·'비대상'·'무주택자'·'불참자' 등.
NEGATING_PREFIXES = "미비무불"
# 원문의 '접두어+단어'. 사람·가구를 가리키는 말로 끝나는 짧은 단어만 본다.
_PREFIXED_TERM = re.compile(rf"(?<![가-힣])([{NEGATING_PREFIXES}])([가-힣]{{1,8}}?(?:자|인|가구|세대|대상))(?![가-힣])")
# 원문에서 그 단어가 제외 문맥인지: 앞 이 글자 수 안에 '제외'가 있거나, 바로 뒤에 '제외'가 온다.
_EXCLUSION_LOOKBEHIND_CHARS = 120
_LEADING_CITATIONS = re.compile(r"(?:\s*\[\d+\])*")
# 답변의 제외 목록 머리말. "제외 대상은 A, B" / "A, B 등은 신청 제외 대상".
_LIST_HEADING_AFTER = re.compile(r"제외\s*대상(?:자)?\s*(?:은|는|으로는|으로|에는|:)")
_LIST_HEADING_BEFORE = re.compile(r"(?:은|는|이|가)\s*(?:신청\s*)?제외\s*대상")
# 목록이 아니라 조건·부정·서술이 섞였다는 표시. 이런 말이 끼면 고치지 않는다.
_NOT_A_LIST = re.compile(r"않|아니|없|지만|해당하지|외에|신청할|신청\s*가능|할\s*수|하고|이고|있고|이며|하며|있으며|[.!?。]")
_MAX_LIST_SPAN = 200
_EXCLUSION_AFTER = re.compile(r"\s*(?:\([^)]{0,30}\))?\s*(?:[은는도]\s*)?(?:신청\s*)?제외")


def _excluded_prefixed_terms(text: str) -> dict[str, str]:
    """원문에서 제외 문맥에 나오는 '접두어+단어'를 {단어: 접두어}로 모은다. 접두어 없는 같은 단어가 있으면 뺀다."""
    found: dict[str, str] = {}
    for match in _PREFIXED_TERM.finditer(text):
        before = text[max(0, match.start() - _EXCLUSION_LOOKBEHIND_CHARS):match.start()]
        if "제외" not in before and not _EXCLUSION_AFTER.match(text, match.end()):
            continue
        prefix, term = match.groups()
        if found.get(term, prefix) != prefix:
            found[term] = ""  # 같은 단어에 다른 접두어가 붙어 있으면 어느 것인지 알 수 없다.
            continue
        found[term] = prefix
    return {
        term: prefix for term, prefix in found.items()
        if prefix and not re.search(rf"(?<![{NEGATING_PREFIXES}]){re.escape(term)}", text)
    }


def _in_exclusion_list(sentence: str, start: int, end: int) -> bool:
    """단어가 '제외 대상' 목록 안에 있는지.

    - 바로 뒤에 '제외'가 온다: "서울시 거주자 제외", "서울시 거주자는 제외돼요"
    - '제외 대상은/으로는 …' 뒤의 목록에 있다: "신청 제외 대상은 서울시 거주자, 재학생 …"
    - '… 등은 신청 제외 대상' 앞의 목록에 있다: "서울시 거주자, 재학생 등은 신청 제외 대상이며"
    목록 사이에 부정·조건·서술이 끼면("제외 대상에 해당하지 않는 서울시 거주자는") 목록으로 보지 않는다.
    """
    if _EXCLUSION_AFTER.match(sentence, end):
        return True
    heading = None
    for heading in _LIST_HEADING_AFTER.finditer(sentence, 0, start):
        pass
    if heading is not None:
        between = sentence[heading.end():start]
        if len(between) <= _MAX_LIST_SPAN and not _NOT_A_LIST.search(between):
            return True
    after = _LIST_HEADING_BEFORE.search(sentence, end)
    if after is not None:
        between = sentence[end:after.start()]
        if len(between) <= _MAX_LIST_SPAN and not _NOT_A_LIST.search(between):
            return True
    return False


def _restore_sentence(sentence: str, terms: dict[str, str]) -> str:
    for term, prefix in terms.items():
        dropped = re.compile(rf"(?<![가-힣]){re.escape(term)}")
        # 뒤에서부터 바꿔야 앞쪽 위치가 밀리지 않는다.
        for match in reversed(list(dropped.finditer(sentence))):
            if _in_exclusion_list(sentence, match.start(), match.end()):
                sentence = sentence[:match.start()] + prefix + sentence[match.start():]
    return sentence


def restore_negated_exclusions(response: dict, contexts: list[dict]) -> dict:
    """제외 대상에서 빠진 부정 접두어를 인용 원문대로 되붙인다. 바꿀 것이 없으면 response를 그대로 돌려준다."""
    texts = {str(context["source_id"]): context.get("text") or "" for context in contexts}
    parts = SENTENCE_BREAK.split(response["answer"])
    changed = False
    for index in range(0, len(parts), 2):
        sentence = parts[index]
        if "제외" not in sentence:
            continue
        # "…적혀 있습니다. [4]"처럼 마침표 뒤에 붙은 출처 번호도 이 문장의 출처다.
        following = parts[index + 2] if index + 2 < len(parts) else ""
        trailing = _LEADING_CITATIONS.match(following).group(0)
        numbers = CITATION.findall(sentence) + CITATION.findall(trailing)
        cited = [texts[number] for number in dict.fromkeys(numbers) if number in texts]
        if not cited:
            continue
        terms: dict[str, str] = {}
        for text in cited:
            for term, prefix in _excluded_prefixed_terms(text).items():
                terms[term] = prefix if terms.get(term, prefix) == prefix else ""
        # 인용한 원문 중 하나라도 접두어 없는 단어를 쓰면 확정할 수 없다.
        terms = {term: prefix for term, prefix in terms.items()
                 if prefix and not any(re.search(rf"(?<![{NEGATING_PREFIXES}]){re.escape(term)}", text) for text in cited)}
        if not terms:
            continue
        restored = _restore_sentence(sentence, terms)
        if restored != sentence:
            parts[index] = restored
            changed = True
    if not changed:
        return response
    # 내용은 로그에 남기지 않는다.
    logger.warning("생성 답변의 제외 대상 표현을 인용 원문의 부정 접두어대로 되돌렸습니다.")
    return {**response, "answer": "".join(parts)}
