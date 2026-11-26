"""신청 주소와 원문·기관 홈페이지의 역할을 정책별로 검증한다."""

import re

from backend.rag.context_builder import valid_source_url

CITATION = re.compile(r"\[(\d+)\]")
# URL 안의 마침표는 문장 경계가 아니다. 쉼표로 나눈 정책 설명도 독립 검증한다.
BREAK = re.compile(r"((?<=[.!?。！？])\s+|\r?\n(?:[ \t]*\r?\n)*|[,;]\s*|\s+(?:하지만|그러나|다만)\s+)")
LINK_CLAIM = re.compile(
    r"신청\s*(?:URL|url|주소|링크|페이지|사이트)"
    r"|(?:홈페이지|웹사이트|https?://|온라인).{0,100}(?:신청|접수)"
    r"|(?:신청|접수).{0,100}(?:홈페이지|웹사이트|https?://)",
)
CAUTION = re.compile(
    r"신청\s*(?:URL|url|주소|링크|페이지|사이트).{0,20}(?:없|미확인|확인되지|아닙|아니|확인.{0,4}필요)"
    r"|(?:홈페이지|웹사이트).{0,12}(?:신청|접수).{0,10}(?:없|않|못|불가)"
    # '신청 공고는 홈페이지에서 확인'은 접수 경로 안내가 아니다.
    r"|(?:홈페이지|웹사이트).{0,15}(?:공고|공지).{0,15}확인"
    r"|(?:신청|모집)\s*(?:공고|공지).{0,80}(?:홈페이지|웹사이트|https?://).{0,80}확인",
)
WEB_ACTION = re.compile(r"(?:신청|접수)(?:할\s*수\s*있|이?\s*가능|하세요|해\s*주세요)")
NO_APPLICATION_LINK = "현재 확인된 전용 신청 주소는 없습니다. 신청 방법은 공식 공고에서 확인해 주세요."


def align_application_links(answer: str, contexts: list[dict], checks: list[dict]) -> tuple[str, bool]:
    """같은 정책의 구조화 신청 주소가 없는 경우에만 웹 신청 단정을 보정한다.

    원문은 수정하지 않는다. 이름과 인용, 같은 문단의 명시된 정책으로 대상을 확정하며, 여러 정책 사이에서
    대상을 확정하지 못하는 신청 안내는 특정 정책의 링크나 인용으로 대체하지 않는다.
    일반 홈페이지 소개·공식 원문 링크·방문 신청 안내는 그대로 둔다.
    """
    by_id = {check["policy_id"]: check for check in checks}
    policy_of = {str(ctx["source_id"]): ctx["policy_id"] for ctx in contexts}
    parts = BREAK.split(answer)
    changed = False
    paragraph_policy = None
    for index in range(0, len(parts), 2):
        if index and re.search(r"\n\s*\n", parts[index - 1]):
            paragraph_policy = None
        clause = parts[index]
        # Markdown 강조가 키워드 사이에 들어와도 같은 의미로 검사한다.
        plain = clause.replace("**", "").replace("`", "")
        # 정책명이 앞 문장/제목에만 나와도 같은 문단의 설명은 그 정책에 속한다.
        # 빈 문단이나 여러 정책의 동시 언급 뒤에는 이전 정책을 추측해서 빌리지 않는다.
        named = {pid for pid, check in by_id.items() if check["policy_name"] in plain}
        if named:
            paragraph_policy = next(iter(named)) if len(named) == 1 else None
        if not LINK_CLAIM.search(plain) or (CAUTION.search(plain) and not WEB_ACTION.search(plain)):
            continue
        numbers = CITATION.findall(clause)
        if any(number not in policy_of for number in numbers):
            continue  # 기존 인용 검증에서 잘못된 번호를 거절한다.
        cited = {policy_of[number] for number in numbers}
        targets = named or cited or ({paragraph_policy} if paragraph_policy else set(by_id))
        if not targets or all(valid_source_url(by_id[pid].get("official_apply_url")) for pid in targets):
            continue
        # 여러 정책을 한 절에 섞으면 특정 정책의 출처를 빌리지 않고 안전하게 거절한다.
        if len(targets) != 1:
            from backend.rag.pipeline import AskError

            raise AskError("LLM_OUTPUT_INVALID")
        pid = next(iter(targets))
        markers = "".join(f"[{n}]" for n in dict.fromkeys(numbers) if policy_of[n] == pid)
        text = NO_APPLICATION_LINK
        if by_id[pid]["reception_status"] == "unknown":
            text += " 현재 모집 일정과 접수 여부도 공식 공고 확인이 필요합니다."
        parts[index] = f"{by_id[pid]['policy_name']}: {text} {markers}".rstrip()
        changed = True
    return "".join(parts), changed
