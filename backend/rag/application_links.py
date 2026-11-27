"""신청 주소와 원문·기관 홈페이지의 역할을 정책별로 검증한다."""

import re

from backend.rag.context_builder import valid_source_url

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
    rf"|(?:신청|접수).{{0,100}}(?:{SITE}|https?://)",
)
CAUTION = re.compile(
    r"신청\s*(?:URL|url|주소|링크|페이지|사이트).{0,20}(?:없|미확인|확인되지|아닙|아니|확인.{0,4}필요)"
    rf"|{SITE}.{{0,12}}(?:신청|접수).{{0,10}}(?:없|않|못|불가)"
    # '신청 공고는 홈페이지에서 확인'은 접수 경로 안내가 아니다.
    rf"|{SITE}.{{0,15}}(?:공고|공지).{{0,15}}확인"
    rf"|(?:신청|모집)\s*(?:공고|공지).{{0,80}}(?:{SITE}|https?://).{{0,80}}확인",
)
WEB_ACTION = re.compile(r"(?:신청|접수)(?:할\s*수\s*있|이?\s*가능|하세요|해\s*주세요)")
# #103에서 합의한 official_apply_url=null 안내 문구. 답변 본문과 같은 해요체로 쓴다.
MISSING_LINK = "직접 신청 링크는 확인되지 않았어요."
NO_APPLICATION_LINK = f"{MISSING_LINK} 신청 방법은 공식 공고에서 확인해 주세요."
UNKNOWN_RECEPTION = "현재 접수 여부는 공식 공고에서 확인해 주세요."
# 정책명·인용·주소 없이 여러 정책을 묶는 마무리 안내('각 신청 페이지에서 확인하세요')의 중립 문구.
GENERIC_NOTICE = "자세한 신청 방법과 일정은 각 정책의 공식 안내를 확인해 주세요."
URL = re.compile(r"https?://|www\.", re.IGNORECASE)
PLURAL = re.compile(rf"(?:각|각각|모든|두)\s*(?:정책|사업|신청|공식|기관|{SITE})")
# '홈페이지 방문'과 구분하려고 오프라인 접수처가 있는 방문만 본다.
OFFLINE_VISIT = re.compile(r"(주민센터|행정복지센터|동사무소|구청|시청|군청)\S{0,3}\s*(?:방문|내방)")


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
        if not LINK_CLAIM.search(plain) or (CAUTION.search(plain) and not WEB_ACTION.search(plain)):
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
        markers = "".join(f"[{n}]" for n in dict.fromkeys(numbers) if policy_of[n] == pid)
        place = _verified_visit(plain, pid, contexts)
        # 원문 근거가 있는 방문 신청은 남기고, 확인되지 않은 온라인 신청 경로만 보정한다.
        text = f"{place} 방문 신청 방법은 확인되지만, {MISSING_LINK}" if place else NO_APPLICATION_LINK
        if by_id[pid]["reception_status"] == "unknown":
            text += f" {UNKNOWN_RECEPTION}"
        parts[index] = head + f"{by_id[pid]['policy_name']}: {text} {markers}".rstrip()
        changed = True
    fixed = "".join(parts)
    # 절을 뺀 경우 남은 구분자 공백만 정리한다.
    return (fixed.strip() if changed else fixed), changed
