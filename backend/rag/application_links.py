"""신청 주소와 원문·기관 홈페이지의 역할을 정책별로 검증한다."""

import re

from backend.rag.context_builder import valid_source_url
from backend.rag.reception_notice import NEAR_PHRASE, has_reception_notice

CITATION = re.compile(r"\[(\d+)\]")
LEADING_CITATIONS = re.compile(r"\s*(?:\[\d+\]\s*)*")
# URL 안의 마침표는 문장 경계가 아니다. 쉼표로 나눈 정책 설명도 독립 검증한다.
# 쉼표는 뒤에 공백이 있을 때만 나누고, 숫자 사이 쉼표('1,000원', '1, 2')는 나누지 않는다.
BREAK = re.compile(
    r"((?<=[.!?。！？])\s+|\r?\n(?:[ \t]*\r?\n)*|(?<!\d),\s+|,\s+(?!\d)|;\s*|\s+(?:하지만|그러나|다만)\s+)"
)
# 정책명 안의 쉼표·세미콜론('자활근로(기초, 차상위)')에서 절이 끊기지 않도록 분리 전에 잠시 치환한다.
PROTECT = "{}"
PROTECTED = re.compile("(\\d+)")
# '누리집'은 홈페이지와 같은 뜻의 공공기관 표기다. 같은 웹 신청 경로 표현으로 검사한다.
SITE = r"(?:홈페이지|웹\s*사이트|누리집)"
LINK_CLAIM = re.compile(
    r"신청\s*(?:URL|url|주소|링크|페이지|사이트)"
    rf"|(?:{SITE}|https?://|온라인).{{0,100}}(?:신청|접수)"
    rf"|(?:신청|접수).{{0,100}}(?:{SITE}|https?://)"
    # '신청은 온라인으로 ○○에서 하거나'처럼 '신청'이 '온라인' 앞에 오는 단정(#122 1번 재검증).
    # 뒤에 할 수 있다·가능·하세요 같은 신청 행위가 있어야 한다('신청 온라인 설명회'는 아니다).
    r"|(?:신청|접수)\S{0,2}\s*온라인\S{0,3}\s.{0,40}?(?:할\s*수\s*있|가능|하세요|해\s*주세요|하면\s*(?:됩|돼)|하거나)",
)
# 주소·홈페이지를 가리키는 표현. 이런 절은 원문에 온라인 신청이 있어도 신청 주소 안내로 보고 보정한다.
ADDRESS_WORD = re.compile(rf"{SITE}|https?://|www\.|신청\s*(?:URL|url|주소|링크|페이지|사이트)", re.IGNORECASE)
CAUTION = re.compile(
    r"신청\s*(?:URL|url|주소|링크|페이지|사이트).{0,20}(?:없|미확인|확인되지|아닙|아니|확인.{0,4}필요)"
    rf"|(?:{SITE}|온라인).{{0,12}}(?:신청|접수).{{0,10}}(?:없|않|못|불가|안\s*됩|안\s*돼)"
    # '접수는 온라인이 아닌 방문으로만', '온라인 대신 우편으로'처럼 온라인 신청을 부정한 절.
    r"|(?:신청|접수|제출)\S{0,3}\s*온라인\S{0,3}\s*(?:제출)?\S{0,3}\s*(?:아니|아닌|대신|없|않|못|불가|안\s*됩|안\s*돼)"
    r"|온라인\S{0,4}\s*(?:신청|접수|제출)\S{0,3}\s*(?:할\s*수\s*없|안\s*됩|안\s*돼)"
    # '신청 공고는 홈페이지에서 확인'은 접수 경로 안내가 아니다.
    rf"|{SITE}.{{0,15}}(?:공고|공지).{{0,15}}확인"
    rf"|(?:신청|모집)\s*(?:공고|공지).{{0,80}}(?:{SITE}|https?://).{{0,80}}확인",
)
WEB_ACTION = re.compile(r"(?:신청|접수)(?:할\s*수\s*있|이?\s*가능|하세요|해\s*주세요)")
# #103에서 합의한 official_apply_url=null 안내 문구. 답변 본문과 같은 해요체로 쓴다.
MISSING_LINK = "직접 신청 링크는 확인되지 않았어요."
REMAINING_METHOD = "신청 방법은 공식 공고에서 확인해 주세요."
NO_APPLICATION_LINK = f"{MISSING_LINK} {REMAINING_METHOD}"
UNKNOWN_RECEPTION = "현재 접수 여부는 공식 공고에서 확인해 주세요."
# 정책명·인용·주소 없이 여러 정책을 묶는 마무리 안내('각 신청 페이지에서 확인하세요')의 중립 문구.
GENERIC_NOTICE = "자세한 신청 방법과 일정은 각 정책의 공식 안내를 확인해 주세요."
URL = re.compile(r"https?://|www\.", re.IGNORECASE)
PLURAL = re.compile(rf"(?:각|각각|모든|두)\s*(?:정책|사업|신청|공식|기관|{SITE})")
# 보정 문구 뒤에 원래 절의 쉼표·세미콜론이 남으면 '확인해 주세요., …'가 된다(#103 후속). 줄바꿈은 그대로 둔다.
NOTICE_COMMA = re.compile(r"(확인해 주세요\.|확인되지 않았어요\.)((?:\s*\[\d+\])*)[ \t]*[,;][ \t]*(\r?\n)?")
# 답변이 스스로 '신청 주소·링크가 확인되지 않았다'고 밝힌 표현. '신청 주소 변경 없이'처럼 다른 뜻은 잡지 않도록
# 주소 단어 바로 뒤(조사·'따로' 등만 허용)에 미확인 서술이 와야 한다.
STATED_MISSING = re.compile(
    r"신청\s*(?:URL|url|주소|링크)(?:은|는|이|가|도)?\s*(?:따로\s*|별도로\s*|아직\s*)?"
    r"(?:확인되지\s*않|제공되지\s*않|나와\s*있지\s*않|적혀\s*있지\s*않|비어\s*있|미확인|없(?:습니다|어요|음|으며|고|다))"
)
# 원문의 온라인 신청 안내: '온라인(고용24) 신청서', '온라인 접수'처럼 온라인 바로 뒤에 신청·접수가 온다.
# '온라인 교육을 신청'처럼 다른 대상을 신청하는 문장은 아니다.
ONLINE_APPLY = re.compile(r"온라인\S{0,8}\s*(?:신청|접수)")
# 한 정책 답변에서 같은 '링크 미확인' 문장이 거듭 나오는지 볼 문장 길이 상한(인용 제외).
MAX_REPEAT_NOTICE = 80
# '홈페이지 방문'과 구분하려고 오프라인 접수처가 있는 방문만 본다.
OFFLINE_VISIT = re.compile(r"(주민센터|행정복지센터|동사무소|구청|시청|군청|고용센터)\S{0,3}\s*(?:방문|내방)")


# 방문 신청을 받지 않는다는 원문·답변을 '방문 신청 가능'으로 뒤집지 않도록 부정 조건을 함께 본다.
VISIT_NEGATION = re.compile(
    r"받지\s*않|하지\s*않|되지\s*않|않습니다|않음|불가|제외|안\s*됩|안\s*되|안\s*됨|할\s*수\s*없|없습니다|없음|금지|아닙니다"
)
SEGMENT = re.compile(r"[\r\n]+|(?<=[.!?。])\s+")


def _verified_visit(clause: str, pid: str, contexts: list[dict]) -> str | None:
    """절의 방문 접수처가 같은 정책 원문에 방문 신청 가능으로 적혀 있을 때만 그 접수처를 돌려준다.

    원문은 문장·줄 단위로 보고, 접수처·방문·신청(접수)이 함께 있으면서 부정 표현이 없는 문장만 근거로 인정한다.
    답변 절 자체가 방문 신청을 부정하는 경우도 방문 안내를 만들지 않는다.
    """
    match = OFFLINE_VISIT.search(clause)
    if not match or VISIT_NEGATION.search(clause):
        return None
    place = match.group(1)
    for ctx in contexts:
        if ctx.get("policy_id") != pid:
            continue
        for segment in SEGMENT.split(str(ctx.get("text") or "")):
            if place in segment and re.search(r"방문|내방", segment) and re.search(r"신청|접수", segment) \
                    and not VISIT_NEGATION.search(segment):
                return place
    return None


def _verified_online(clause: str, pid: str, contexts: list[dict]) -> bool:
    """주소·홈페이지 없이 '온라인으로 신청'만 말한 절이 같은 정책 원문의 온라인 신청 안내와 맞으면 True.

    신청 주소를 안내하는 것이 아니라 신청 방법 설명이므로 보정하지 않는다(#158 인계: 온라인 신청 절차 보존).
    """
    if "온라인" not in clause or ADDRESS_WORD.search(clause) or VISIT_NEGATION.search(clause):
        return False
    segments = [segment for ctx in contexts if ctx.get("policy_id") == pid
                for segment in SEGMENT.split(str(ctx.get("text") or ""))]
    # 원문 어디에든 '온라인 신청은 받지 않는다'가 있으면 온라인 신청을 인정하지 않는다(PR #161 리뷰).
    if any(ONLINE_APPLY.search(segment) and VISIT_NEGATION.search(segment) for segment in segments):
        return False
    return any(ONLINE_APPLY.search(segment) for segment in segments)


def _states_missing_only(clause: str) -> bool:
    """'신청 주소·링크가 확인되지 않았다'만 말하고 웹 신청 단정은 없는 절."""
    if not STATED_MISSING.search(clause) or WEB_ACTION.search(clause):
        return False
    return not LINK_CLAIM.search(STATED_MISSING.sub("", clause))


def _missing_notice_only(sentence: str) -> bool:
    """문장이 '신청 주소·링크가 확인되지 않았다'는 말만 담고 있으면 True.

    미확인 서술 뒤에는 어미('았어요', '습니다' 등)만 올 수 있다. '없고 주민센터 방문으로만 접수해요',
    '없으며 신분증이 필요해요', '아직 없고 3월에 공개'처럼 다른 정보가 이어지면 False다(PR #161 재리뷰).
    """
    body = CITATION.sub("", sentence).replace("**", "").strip()
    matches = list(STATED_MISSING.finditer(body))
    if not body or len(body) > MAX_REPEAT_NOTICE or not matches or not _states_missing_only(body):
        return False
    gaps = [body[a.end():b.start()] for a, b in zip(matches, matches[1:])]
    head, tail = body[:matches[0].start()], body[matches[-1].end():]
    return (len(head) <= 20 and all(len(gap) <= 15 for gap in gaps)
            and re.fullmatch(r"\s*(?:았|었)?(?:어요|습니다|어|다|요|음)?\s*[.!?。]?\s*", tail) is not None)


def _drop_repeated_missing_notice(answer: str) -> tuple[str, bool]:
    """한 정책 답변에서 '신청 주소·링크가 확인되지 않았다'는 짧은 문장이 거듭 나오면 첫 문장만 남긴다.

    LLM이 '공식 신청 주소는 확인되지 않았어요. … 직접 신청 링크는 확인되지 않았습니다.'처럼 같은 안내를
    연달아 쓰는 경우다(#122 1번 재검증 인천). 다른 정보가 섞인 문장은 두고, 뺀 문장의 인용 번호는
    같은 안내를 하는 첫 문장으로 옮긴다(엉뚱한 앞 문장의 근거처럼 보이지 않게).
    """
    pieces = re.split(r"((?<=[.!?。])\s+|\n+)", answer)
    kept = None
    moved: list[str] = []
    last_dropped = None
    for index in range(0, len(pieces), 2):
        sentence = pieces[index]
        citations_only = sentence.strip() and not CITATION.sub("", sentence).strip()
        if citations_only and last_dropped == index - 2:
            # 빠진 문장 뒤에 떨어져 있던 인용('…않았습니다. [2]')도 첫 문장으로 옮긴다.
            moved += CITATION.findall(sentence)
            pieces[index] = pieces[index - 1] = ""
            continue
        if not _missing_notice_only(sentence):
            continue
        if kept is None:
            kept = index
            continue
        moved += CITATION.findall(sentence)
        pieces[index] = ""
        _drop_one_separator(pieces, index)
        last_dropped = index
    if last_dropped is None:
        return answer, False
    # 첫 문장의 인용이 마침표 뒤에 따로 있으면('…않았어요. [1][2]') 그 인용 묶음에 붙인다.
    target = kept
    if (kept + 2 < len(pieces) and "\n" not in pieces[kept + 1] and pieces[kept + 2].strip()
            and not CITATION.sub("", pieces[kept + 2]).strip()):
        target = kept + 2
    extra = "".join(f"[{n}]" for n in dict.fromkeys(moved) if n not in CITATION.findall(pieces[target]))
    if extra:
        # 첫 문장의 인용 묶음 끝(없으면 문장 끝)에 붙인다. 인용 번호는 문서 연결을 그대로 유지한다(PR #140 검증).
        pieces[target] = re.sub(r"((?:\[\d+\])+)(?=[^\[]*$)|\s*$", lambda m: (m.group(1) or " ") + extra,
                                pieces[target], count=1)
    fixed = "".join(pieces)
    fixed = re.sub(r"[ \t]+(?=\n|$)", "", fixed)
    fixed = re.sub(r"[ \t]{2,}", " ", fixed)
    return _merge_adjacent_citations(re.sub(r"\n[ \t]*(?:\n[ \t]*){2,}", "\n\n", fixed).strip()), True


def _drop_one_separator(parts: list[str], index: int) -> None:
    """빈 절 앞뒤 구분자 중 하나만 지운다. 줄바꿈이 적은 쪽을 지워야 문단 구분(\\n\\n)이 남고,
    목록 항목 하나가 빠질 때도 빈 줄이 생기지 않는다(PR #161 4차 리뷰)."""
    before = parts[index - 1] if index else None
    after = parts[index + 1] if index + 1 < len(parts) else None
    if before is None and after is None:
        return
    if after is None or (before is not None and before.count("\n") <= after.count("\n")):
        parts[index - 1] = ""
    else:
        parts[index + 1] = ""


def _citation_anchor(parts: list[str], index: int, notice: int | None, pid: str,
                     policy_of: dict[str, str], head_citations: bool = False) -> int | None:
    """빠진 절(index)의 인용을 옮길 절 위치. 같은 정책 링크 안내 문장을 먼저, 없으면 바로 앞 절을 본다.

    안내 문장 바로 뒤에 인용만 따로 있으면('…않았어요. [1]') 그 인용 조각을 쓴다.
    끝 인용 묶음의 출처가 모두 같은 정책(pid)인 절만 대상이다. 다른 정책 문장의 근거에 섞이지 않게 한다(PR #161 5차 리뷰).
    """
    def citations_only(at: int) -> bool:
        return bool(parts[at].strip()) and not CITATION.sub("", parts[at]).strip()

    if head_citations:
        # 빠진 절 머리에 앞 문장의 인용('없어요. [1] 홈페이지…')이 있으면 그 자리에 함께 남겨 '[1][2]'로 둔다(7차 리뷰).
        return None
    low, high = sorted((notice, index)) if notice is not None else (index, index)
    same_paragraph = not any(re.search(r"\n[ \t]*\n", parts[i]) for i in range(low + 1, high, 2))
    if notice is not None and notice != index and same_paragraph and not head_citations:
        # 안내 문장 바로 뒤(같은 줄이나 다음 줄)에 인용만 따로 있으면 그 조각, 아니면 안내 문장 자체(인용이 없어도 붙인다).
        # 안내 문장이 빠진 절보다 뒤에 있어도 옮긴다('[2] 서류 [1]…'처럼 답변이 인용으로 시작하지 않게, PR #161 6차 리뷰).
        # 다른 문단의 안내 문장으로는 멀리 옮기지 않는다(7차 리뷰).
        after_notice = notice + 2
        if after_notice < len(parts) and after_notice != index and citations_only(after_notice) \
                and not re.search(r"\n[ \t]*\n", parts[notice + 1]) \
                and _same_policy_group(parts[after_notice], pid, policy_of):
            return after_notice
        if _same_policy_group(parts[notice], pid, policy_of, allow_empty=True):
            return notice
    at = index - 2
    if at >= 0 and _same_policy_group(parts[at], pid, policy_of):
        return at
    return None


def _same_policy_group(part: str, pid: str, policy_of: dict[str, str], *, allow_empty: bool = False) -> bool:
    """절 끝 인용 묶음의 출처가 모두 pid이면 True. allow_empty면 끝에 인용이 없어도 True."""
    group = re.search(r"((?:\[\d+\])+)[.!?。！？]?\s*$", part)
    if not group:
        return allow_empty and bool(part.strip())
    return all(policy_of.get(n) == pid for n in CITATION.findall(group.group(1)))


def _append_citations(sentence: str, markers: str) -> str | None:
    """문장 끝 인용 묶음('…해요 [1].', '[2][1]')에 인용을 더한다. 끝에 인용 묶음이 없으면 None."""
    match = re.search(r"((?:\[\d+\])+)([.!?。！？]?)(\s*)$", sentence)
    if not match:
        if not markers:
            return None
        # 끝에 인용이 없는 안내 문장은 보정 문구와 같은 형식('…주세요. [2]')으로 문장부호 뒤에 붙인다.
        # 이렇게 해야 NOTICE_COMMA가 뒤 쉼표를 정리하고, 다음 절 머리 인용과도 묶음으로 합쳐진다(PR #161 7차 리뷰).
        stripped = sentence.rstrip()
        return f"{stripped} {markers}" + sentence[len(stripped):]
    group = "".join(f"[{n}]" for n in dict.fromkeys(CITATION.findall(match.group(1) + markers)))
    return sentence[:match.start()] + group + match.group(2) + match.group(3)


def _merge_adjacent_citations(text: str) -> str:
    """보정으로 붙은 인용 묶음이 바로 앞 묶음과 겹치면('[2] [2]', '[1][2] [2]') 새 번호만 남긴다."""
    def merge(match: re.Match[str]) -> str:
        first = CITATION.findall(match.group(1))
        extra = [n for n in dict.fromkeys(CITATION.findall(match.group(2))) if n not in first]
        return match.group(1) + "".join(f"[{n}]" for n in extra)
    previous = None
    while previous != text:
        previous = text
        text = re.sub(r"((?:\[\d+\])+)[ \t]+((?:\[\d+\])+)", merge, text)
        # 한 묶음 안에서 같은 번호가 이어지면('[1][1]') 하나만 남긴다.
        text = re.sub(r"(?:\[\d+\]){2,}", lambda m: "".join(
            f"[{n}]" for n in dict.fromkeys(CITATION.findall(m.group(0)))), text)
    return text


def _named_policies(text: str, by_id: dict[str, dict]) -> set[str]:
    """절에 적힌 정책을 긴 이름부터 찾는다.

    '포항시 장애수당'·'K-패스 청년'처럼 긴 정책명 안에 들어 있는 짧은 정책명은 그 위치에서는 후보로 보지 않는다.
    짧은 정책명이 긴 이름 바깥에 따로 적혀 있으면 그대로 후보에 넣는다.
    """
    named = set()
    rest = text
    for pid, check in sorted(by_id.items(), key=lambda item: -len(item[1]["policy_name"])):
        name = check["policy_name"]
        if name and name in rest:
            named.add(pid)
            rest = rest.replace(name, "\u0000")
    return named


def align_application_links(answer: str, contexts: list[dict], checks: list[dict]) -> tuple[str, bool]:
    """같은 정책의 구조화 신청 주소가 없는 경우에만 웹 신청 단정을 보정한다.

    원문은 수정하지 않는다. 이름과 인용, 같은 문단의 명시된 정책으로 대상을 확정하며, 여러 정책 사이에서
    대상을 확정하지 못하는 신청 안내는 특정 정책의 링크나 인용으로 대체하지 않는다. 주소를 직접 제시하면 거절하고,
    주소 없이 여러 정책을 묶은 안내(마무리 문구, '각 홈페이지에서 신청 [1][2]')는 그 절만 중립 문구로 보정한다.
    일반 홈페이지 소개·공식 원문 링크·방문 신청 안내는 그대로 두고, 방문·온라인 혼합 문장에서는
    같은 정책 원문으로 확인되는 방문 접수처만 남긴다.
    """
    by_id = {check["policy_id"]: check for check in checks}
    policy_of = {str(ctx["source_id"]): ctx["policy_id"] for ctx in contexts}
    guarded = sorted({c["policy_name"] for c in checks if re.search(r"[,;]", c["policy_name"] or "")},
                     key=len, reverse=True)
    protected = answer
    for number, name in enumerate(guarded):
        protected = protected.replace(name, PROTECT.format(number))
    parts = BREAK.split(protected)
    changed = False
    paragraph_policy = None
    # 같은 정책의 신청 안내는 한 답변에 한 번만 넣는다('…없고, …에서 신청' 두 절이 같은 문장으로 반복되지 않게).
    noticed: set[str] = set()
    # 한 정책만 다루는 답변은 정책명을 붙이지 않는다(접수 문구 보정과 같은 기준, 계약 §7 4·11번).
    single = len(by_id) == 1
    # 한 정책 답변이 이미 '신청 주소·링크는 확인되지 않았다'고 밝혔으면 보정 안내를 또 넣지 않고
    # 웹 신청 단정 절만 뺀다(#122 후속: '직접 신청 링크는 확인되지 않았어요'가 두 번 나오던 문제).
    # 웹 신청 단정이 같이 있는 절('주소는 확인되지 않았지만 홈페이지에서 신청')은 안내가 있다고 보지 않는다(PR #161 리뷰).
    # 정책별 링크 안내 문장 위치. 뺀 문장의 인용을 이 문장으로 옮긴다.
    notice_at: dict[str, int] = {}
    stated = [i for i in range(0, len(parts), 2) if _states_missing_only(parts[i].replace("**", ""))]
    if single and stated:
        noticed.update(by_id)
        notice_at = {pid: stated[0] for pid in by_id}
    for index in range(0, len(parts), 2):
        parts[index] = PROTECTED.sub(lambda m: guarded[int(m.group(1))], parts[index])
        if index and re.search(r"\n\s*\n", parts[index - 1]):
            paragraph_policy = None
        # '...확인해 주세요. [1][3] 다음 문장'처럼 절 맨 앞의 인용은 앞 문장의 출처이므로 그대로 둔다.
        lead = LEADING_CITATIONS.match(parts[index])
        head, clause = parts[index][:lead.end()], parts[index][lead.end():]
        # Markdown 강조가 키워드 사이에 들어와도 같은 의미로 검사한다.
        plain = clause.replace("**", "").replace("`", "")
        # 정책명이 앞 문장/제목에만 나와도 같은 문단의 설명은 그 정책에 속한다.
        # 빈 문단이나 여러 정책의 동시 언급 뒤에는 이전 정책을 추측해서 빌리지 않는다.
        named = _named_policies(plain, by_id)
        if named:
            paragraph_policy = next(iter(named)) if len(named) == 1 else None
        cautious = CAUTION.search(plain) or STATED_MISSING.search(plain)
        if not LINK_CLAIM.search(plain) or (cautious and not WEB_ACTION.search(plain)):
            continue
        numbers = CITATION.findall(clause)
        if any(number not in policy_of for number in numbers):
            continue  # 기존 인용 검증에서 잘못된 번호를 거절한다.
        cited = {policy_of[number] for number in numbers}
        # '각 신청 페이지'처럼 여러 정책을 가리키면 같은 문단의 마지막 정책 하나로 좁히지 않는다.
        unscoped = set(by_id) if PLURAL.search(plain) or not paragraph_policy else {paragraph_policy}
        targets = named or cited or unscoped
        scope = None if named or PLURAL.search(plain) else paragraph_policy
        if cited and scope and scope not in cited:
            # 제목·문단의 정책과 인용 정책이 다르면 인용 정책의 주소가 유효하다는 이유로 넘어가지 않는다.
            # 둘 중 전용 신청 주소가 없는 정책이 하나뿐이면 그 정책 기준으로 보정한다(다른 정책 인용은 빌리지 않음).
            targets = cited | {scope}
            unverified = {pid for pid in targets if not valid_source_url(by_id[pid].get("official_apply_url"))}
            if len(unverified) == 1:
                targets = unverified
        if not targets or all(valid_source_url(by_id[pid].get("official_apply_url")) for pid in targets):
            continue
        if len(targets) != 1:
            # 어느 정책의 주소인지 모르는 URL을 직접 제시하면 출처 혼입이므로 거절한다.
            if URL.search(plain):
                from backend.rag.pipeline import AskError

                raise AskError("LLM_OUTPUT_INVALID")
            # 주소 없이 여러 정책을 묶은 신청 안내는 그 절만 중립 문구로 바꾸고 나머지 답변은 유지한다.
            # 근거 문맥이 없는 정책이 섞이면 "각 정책의 공식 안내"도 주장하지 않는다.
            text = GENERIC_NOTICE if targets <= set(policy_of.values()) else ""
            if named or cited:
                # 이름·인용으로 특정된 정책 중 전용 신청 주소가 없는 정책만 구조화 정보대로 밝히고,
                # 원래 인용은 각자 자기 정책 근거를 가리키므로 그대로 둔다.
                missing = [pid for pid in by_id if pid in targets
                           and not valid_source_url(by_id[pid].get("official_apply_url"))]
                notes = " ".join(f"{by_id[pid]['policy_name']}: {MISSING_LINK}" for pid in missing)
                markers = "".join(f"[{n}]" for n in dict.fromkeys(numbers))
                text = " ".join(filter(None, (text, notes, markers)))
            parts[index] = head + text
            changed = True
            continue
        pid = next(iter(targets))
        if _verified_online(plain, pid, contexts):
            continue  # 원문으로 확인되는 온라인 신청 방법 설명은 그대로 둔다.
        markers = "".join(f"[{n}]" for n in dict.fromkeys(numbers) if policy_of[n] == pid)
        if pid in noticed:
            # 이미 같은 정책 안내가 있으면 이 절은 인용만 남긴다.
            parts[index] = head + markers
            if not CITATION.sub("", parts[index]).strip():
                before = parts[index - 1] if index else ""
                after = parts[index + 1] if index + 1 < len(parts) else ""
                if re.fullmatch(r"\s*[,;]\s*", before):
                    # 문장 뒷절이 빠지면 앞절이 '필요하고,'로 끊긴다. 링크 안내는 이미 있으므로 방법 안내만 넣는다(PR #161 리뷰).
                    # 앞절이 방금 넣은 보정 안내면 인용만 붙인다(쉼표는 NOTICE_COMMA가 정리).
                    if REMAINING_METHOD not in parts[index - 2]:
                        parts[index] = head + f"{REMAINING_METHOD} {markers}".rstrip()
                elif re.fullmatch(r"\s*[,;]\s*", after):
                    # 앞절이 빠지면 뒤 쉼표를 지워야 '확인됩니다., 다음 절'이 되지 않는다(#122 후속).
                    parts[index + 1] = ""
                elif markers and (anchor := _citation_anchor(parts, index, notice_at.get(pid), pid, policy_of,
                                                               head_citations=bool(head.strip()))) is not None:
                    # 문장 전체가 빠지면 그 인용은 같은 정책의 링크 안내 문장(없으면 바로 앞 문장)의 인용 묶음으로
                    # 옮긴다. 반복 안내 제거와 같은 기준이다(인용 번호 유지, PR #140 검증).
                    parts[anchor] = _append_citations(parts[anchor], markers)
                    parts[index] = head
                    _drop_one_separator(parts, index)
                elif index >= 2 and markers and (punct := re.search(r"[.!?。！？]\s*$", clause)) \
                        and not re.match(r"\s*[,;]", after) \
                        and not re.search(r"[.!?。！？](?:\s*\[\d+\])*\s*$", parts[index - 2]):
                    # 빠진 절이 문장 끝이었으면 마침표를 남겨야 앞 절이 문장으로 끝난다('신분증이에요 [1][2].').
                    parts[index] = head + markers + punct.group(0).strip()
                    if "\n" in before:
                        parts[index - 1] = " "
                elif markers and "\n" in before:
                    # 인용만 남은 문단은 앞 문단 끝에 붙인다.
                    parts[index - 1] = " "
                elif not markers and "\n" in before and "\n" in after:
                    _drop_one_separator(parts, index)  # 목록 항목이 빠질 때 빈 줄 없이, 문단 구분은 유지.
                elif not markers and index and "\n" not in before:
                    parts[index - 1] = ""
            changed = True
            continue
        noticed.add(pid)
        notice_at.setdefault(pid, index)
        place = _verified_visit(plain, pid, contexts)
        # 원문 근거가 있는 방문 신청은 남기고, 확인되지 않은 온라인 신청 경로만 보정한다.
        text = f"{place} 방문 신청 방법은 확인되지만, {MISSING_LINK}" if place else NO_APPLICATION_LINK
        # 답변에 이미 접수 미확인 안내가 있으면 같은 문구를 또 붙이지 않는다.
        # 문구가 정책별로 빠짐없이 들어갔는지는 reception_notice가 다시 보장한다(#103·#147).
        if by_id[pid]["reception_status"] == "unknown" and not (NEAR_PHRASE.search(answer) or has_reception_notice(answer)):
            text += f" {UNKNOWN_RECEPTION}"
        label = "" if single else f"{by_id[pid]['policy_name']}: "
        parts[index] = head + f"{label}{text} {markers}".rstrip()
        changed = True
    fixed = "".join(parts)
    if single and not valid_source_url(next(iter(by_id.values())).get("official_apply_url")):
        fixed, deduped = _drop_repeated_missing_notice(fixed)
        changed = changed or deduped
    if changed:
        fixed = NOTICE_COMMA.sub(lambda m: m.group(1) + m.group(2) + (m.group(3) or " "), fixed)
        # 문단 하나가 통째로 빠지면 빈 줄이 겹친다(PR #161 리뷰).
        fixed = re.sub(r"\n[ \t]*(?:\n[ \t]*){2,}", "\n\n", fixed)
        # 빠진 절의 인용을 앞에 붙이면서 같은 번호가 이어지면 하나로 합친다.
        fixed = _merge_adjacent_citations(fixed)
        # 문단 첫 절이 빠지면 줄 앞에 구분 공백 한 칸이 남는다. 들여쓰기(두 칸 이상)·인용문·코드는 건드리지 않는다.
        fixed = re.sub(r"(\n) (?=[^\s>|`\-*+\d])", r"\1", fixed)
    # 절을 뺀 경우 남은 구분자 공백만 정리한다.
    return (fixed.strip() if changed else fixed), changed
