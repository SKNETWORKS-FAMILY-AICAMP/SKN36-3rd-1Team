"""정책 검색, 조건 검사, 답변 생성과 출처 검증을 연결한다."""

from collections.abc import Callable
from json import JSONDecodeError
import logging
import re

from httpx import NetworkError, TimeoutException
from pydantic import ValidationError

from backend.api.schemas import AskRequest, AskResponse, Contact, EligibilityOutput, GeneratorOutput
from backend.rag.context_builder import rank_citable_contexts, select_contexts, valid_source_url
from database.eligibility_fields import SCOPED_ELIGIBILITY_FIELDS
from backend.eligibility.checker import certain_required_and_failure, application_schedule
from backend.rag.source_sections import application_details
from database.unknown_group_scope import (
    can_preserve_scoped_failed_root,
    normalize_quality_affected_attributes,
    scoped_income_quality_attributes,
)
from database.region_search import ALIASES, SIDOS

logger = logging.getLogger(__name__)
CITATION = re.compile(r"\[(\d+)\]")
# 검색 계층(data_quality.affected_attributes)·Checker와 같은 공통 상수("all" 제외).
# 목록 밖의 값과 "all"은 영향 범위를 확정할 수 없으므로 정책 전체를 보류한다.
SCOPED_CONDITION_FIELDS = SCOPED_ELIGIBILITY_FIELDS
QUALITY_REASON = "데이터가 불확실하여 공식 공고에서 조건을 확인해야 합니다."
MISSING_PROFILE_REASON = "입력한 정보가 없어 자동 판정을 보류했습니다. 정보를 입력하거나 공식 공고에서 확인해 주세요."
INCOME_REASON = "소득 기준(개인·가구 구분)이 확정되지 않아 공식 공고에서 확인해야 합니다."
# 화면(frontend/src/lib/answerView.ts)이 이 문장으로 일반 보류 안내를 알아본다. 바꾸면 화면도 함께 바꾼다.
HOLD_NOTICE_TEXT = "자동 판정을 보류한 조건이 있습니다. 공식 공고를 확인해 주세요."
# 신청 상태(before/open/closed/unknown)는 Checker 단일 책임이다. Pipeline은 날짜를 판정하지 않는다.
CLOSED = "closed"
# 정책명 직접 언급 판별: 공백·문장부호를 지운 정책명 전체가 질문에 들어 있을 때만 인정한다.
# 너무 짧은 이름은 일반 단어와 겹칠 수 있어 제외한다.
NAME_NOISE = re.compile(r"[\W_]+")
MIN_NAMED_POLICY_LENGTH = 4
# 생성 본문의 접수·자격 단정이 Checker 판정과 충돌하면 확인된 상태 문구로 바꾼다(문장 단위).
SENTENCE_BREAK = re.compile(r"((?<=[.!?。！？])\s+|\n+)")
OPEN_CLAIM = re.compile(
    r"(?:지금|현재|바로|오늘|당장)\s*\S{0,8}?\s*(?:신청|접수)\S{0,6}?\s*(?:수\s*)?(?:있|가능)"
    r"|접수\s*(?:중|하고\s*있)|신청\s*(?:기간|접수)\s*(?:중|입니다)"
)
ELIGIBLE_CLAIM = re.compile(
    r"자격(?:을|이|\s*요건을|\s*요건이)?\s*(?:모두\s*)?(?:충족|갖추|갖췄)"
    r"|신청\s*대상(?:에|이)\s*(?:해당|맞)|(?:신청|지원)\s*자격이\s*(?:있|됩)"
)
# 부정·유보 표현이 있는 문장은 단정이 아니므로 바꾸지 않는다.
HEDGED_CLAIM = re.compile(r"없|않|못|불가|어렵|여부|인지|단정|아직|추가|확인(?:이|해|할|되)|수도|일\s*수")
RECEPTION_NOTICE = {
    "closed": "접수가 마감됐어요.",
    "before": "아직 접수가 시작되지 않았어요.",
    "unknown": "현재 접수 여부는 공식 공고에서 확인이 필요해요.",
}
ELIGIBILITY_NOTICE = "신청 자격은 공식 공고에서 추가 확인이 필요해요."
INELIGIBLE_NOTICE = "입력하신 정보로는 맞지 않는 조건이 있어요. 신청 자격은 공식 공고에서 확인해 주세요."
# Checker의 eligible은 개별 조건 충족일 뿐 전체 신청 자격 보장이 아니다.
PARTIAL_ELIGIBLE_NOTICE = "확인된 조건은 충족하지만, 전체 신청 자격은 공식 공고에서 확인이 필요해요."
OPEN_CONFIRMED = "접수 중이에요."
# 유보는 절 단위로 판단한다. "서류는 확인이 필요하지만, 지금 신청할 수 있어요"의 단정을 놓치지 않기 위해서다.
CLAUSE_BREAK = re.compile(r"(,\s*|;\s*|\s+(?:하지만|그러나|다만|그런데)\s+)")
# 문장 끝의 출처 번호·마침표. 여러 절 문장에서는 특정 절이 아니라 문장 전체의 출처로 본다.
SENTENCE_TAIL = re.compile(r"\s*(?:\[\d+\]\s*)*[.!?。！？]*\s*$")
# 절 첫 단어의 주제 조사(은/는). 주제가 둘 이상이면 여러 정책을 나눠 설명하는 문장으로 본다.
TOPIC = re.compile(r"^\s*(\S+?)(?:은|는)\s")


class AskError(RuntimeError):
    """API가 사용자에게 전달할 오류 코드."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


# [문의처] 블록은 다음 [...] 태그(보통 [출처 URL]) 전까지다. 블록이 없으면 None.
# 줄 시작에서만 인정한다 — scripts/build_clean_dataset.py의 iter_document_chunks가 청크를 보호하는
# 범위와 정의를 맞춰야, 그 쪼개기가 지켜주지 않는 "줄 중간의 [문의처]"를 여기서도 블록으로 보지 않는다.
_CONTACT_BLOCK = re.compile(r"^\[문의처\]\s*(.*?)(?=\n\n\[|\Z)", re.DOTALL | re.MULTILINE)
# 기관명 뒤에서 전화번호를 분리하는 구분자. 출처마다 다르다: 정부24는 "/", 복지로는 "(" (닫는 괄호 없음).
# 숫자에 바로 붙은 "("(예: "043-913-2352(시스템)")은 전화번호 뒤의 부가 설명이지 구분자가 아니므로 제외한다.
_CONTACT_SEPARATOR = re.compile(r"(?<!\d)[/(]")
# 기관명이 숫자로 끝나는 경우(예: "학교폭력117(1588-7179)")의 구분자 — 위 구분자가 모두 숫자 뒤에
# 붙어 있어서 제외됐을 때만 대신 쓴다. 뒤에 유효한 전화번호가 없으면 결국 같은 결과(분리 실패)로 떨어진다.
_ANY_CONTACT_SEPARATOR = re.compile(r"[/(]")
# 구분자 뒤 텍스트에서 실제 전화번호만 뽑는다("국번없이 132", "각 기관 120 센터"처럼 설명이 섞여도 숫자만 가져온다).
_PHONE_IN_TAIL = re.compile(r"\d[\d\-]{1,}\d|\d{2,4}")
# 쉼표로 분리된 조각이 새 기관이 아니라 앞 전화번호의 내선/부가 번호인 경우
# (예: "...(시스템), 2258(사업"의 "2258(사업"). 숫자로 시작해 바로 "("가 붙는 짧은 조각만 해당하되,
# 그 조각 자체로 유효한 기관·전화번호 쌍을 만들 수 있으면(예: "120(위기상담센터) (1588-9191") 내선이 아니라
# 새 기관이므로 오탐을 막기 위해 제외한다.
_EXTENSION_CONTINUATION_PREFIX = re.compile(r"^\d{2,6}\(")


def _split_agency_phone(fragment: str) -> tuple[str, str | None]:
    """"기관명(/|() 전화번호" 한 건을 기관명과 전화번호로 나눈다. 중첩 괄호가 있으면 마지막 구분자를 쓴다."""
    separators = list(_CONTACT_SEPARATOR.finditer(fragment))
    if not separators:
        # 전부 숫자 뒤의 "("라서 제외됐을 수 있다(기관명이 숫자로 끝나는 경우). 뒤에 유효한 전화번호가
        # 있으면 실제 구분자로 본다 — 없으면 아래와 동일하게 분리 실패로 떨어진다.
        separators = list(_ANY_CONTACT_SEPARATOR.finditer(fragment))
        if not separators:
            return fragment, None
    split_at = separators[-1].start()
    agency = fragment[:split_at].strip()
    tail = fragment[split_at + 1:].strip()
    phone_match = _PHONE_IN_TAIL.search(tail) if tail else None
    if not agency or phone_match is None:
        return fragment.rstrip(")").strip(), None
    trailing = tail[phone_match.end():].strip()
    if trailing:
        # 숫자 뒤에도 설명이 남아 있으면(예: "각 기관 120 센터", "1566-3232(단축번호 1번")
        # 숫자만 뽑으면 맥락을 잃으므로 구절 전체를 보존한다.
        return agency, tail
    return agency, phone_match.group(0)


def _is_extension_continuation(fragment: str) -> bool:
    """내선/부가 번호 조각인지 판단한다. 자체적으로 유효한 기관·전화번호 쌍이 나오면 새 기관으로 본다."""
    if not _EXTENSION_CONTINUATION_PREFIX.match(fragment):
        return False
    _, phone = _split_agency_phone(fragment)
    return phone is None


def parse_contacts(block: str | None) -> list[Contact]:
    """[문의처] 블록 본문을 기관·전화번호 목록으로 나눈다.

    원문은 출처마다 구분자가 다르고(/ 또는 열기만 하는 괄호), 여러 기관을 잇는 구분자도 다르다
    (쉼표 또는 "||", 최대 17건까지 확인됨). 전화번호 없는 기관도 있다. 어느 쪽도 임의로 지어내지
    않고 원문 글자 그대로의 기관명·전화번호만 담는다.
    """
    if not block or not block.strip():
        return []
    contacts: list[Contact] = []
    delimiter = "||" if "||" in block else ","
    for fragment in block.split(delimiter):
        fragment = fragment.strip().rstrip(")").strip()
        if not fragment:
            continue
        if contacts and _is_extension_continuation(fragment):
            previous = contacts[-1]
            previous.phone = f"{previous.phone}, {fragment}" if previous.phone else fragment
            continue
        agency, phone = _split_agency_phone(fragment)
        if agency:
            contacts.append(Contact(agency=agency, phone=phone))
    return contacts


def contacts_from_text(text: str) -> list[Contact]:
    """근거 원문 전체에서 [문의처] 블록을 찾아 파싱한다. 블록이 없으면 빈 리스트."""
    match = _CONTACT_BLOCK.search(text or "")
    return parse_contacts(match.group(1)) if match else []


def _collect_contacts(contexts) -> list[Contact]:
    contacts = {}
    for context in contexts:
        for contact in contacts_from_text(context["text"]):
            key = (contact.agency, contact.phone)
            if key not in contacts:
                contacts[key] = contact.model_copy(update={
                    "document_id": context["document_id"],
                    "chunk_id": context["chunk_id"],
                    "source_url": context["url"] if valid_source_url(context["url"]) else None,
                })
    return list(contacts.values())


def _hold_selected_policy_region(policy: dict, check: dict, profile_region: str | None) -> None:
    """선택 정책은 지역 필터 없이 찾으므로 정책의 대상 지역(AVAILABLE_IN)과 입력 지역을 직접 대조한다.

    v1.4.0 데이터는 지역 제한을 조건 트리(REGION)가 아니라 지역 관계로만 갖고 있어 Checker가 지역을 판정하지 않는다.
    입력 지역이 대상 지역·전국에 없으면 확인 필요로 보류한다. 확정 불충족으로 단정하지 않는다.
    """
    regions = (policy.get("graph") or {}).get("regions") or []
    names = {str(region.get("name")).strip() for region in regions
             if isinstance(region, dict) and str(region.get("name") or "").strip()}
    if not profile_region or not names or "전국" in names or profile_region in names:
        return
    conditions = check["conditions"]
    region_rows = [row for row in conditions if row["field"].lower() == "region"]
    if any(row["status"] == "ineligible" for row in region_rows):
        return
    mismatch = (f"이 정책의 대상 지역은 {', '.join(sorted(names))}이고 입력한 지역({profile_region})과 달라요. "
                "거주 요건을 공식 공고에서 확인해 주세요.")
    # 계약 §4.3: Checker가 needs_check로 준 사유는 바꾸지 않고, 확정 상태만 보류로 내리며 사유를 뒤에 덧붙인다.
    eligible_rows = [row for row in region_rows if row["status"] == "eligible"]
    for row in eligible_rows:
        row["status"] = "needs_check"
        row["reason"] += f" {mismatch}"
    if not eligible_rows:
        # 기존 지역 행이 없거나 needs_check뿐이면 그 사유는 그대로 두고 지역 불일치 행을 따로 앞에 둔다.
        conditions.insert(0, {"field": "region", "label": "거주 지역", "status": "needs_check", "reason": mismatch})
    root = next((row for row in reversed(conditions) if row["field"].lower() == "eligibility"), None)
    if root is not None and root["status"] == "eligible":
        root["status"] = "needs_check"
        root["reason"] += " 거주 지역 확인이 필요해 종합 판정을 보류합니다."


def _policy_check(policy: dict, raw: dict, profile: dict, context: dict) -> dict:
    try:
        result = EligibilityOutput.model_validate(raw)
    except ValidationError as exc:
        raise AskError("ELIGIBILITY_OUTPUT_INVALID") from exc
    if result.policy_id != policy["policy_id"]:
        raise AskError("ELIGIBILITY_OUTPUT_INVALID")

    graph = policy.get("graph") or {}
    graph_policy = graph.get("policy") or {}
    structure = graph.get("eligibility") or {}
    quality = policy.get("data_quality") or {}
    scoped_unknown = scoped_income_quality_attributes(structure, quality)
    if scoped_unknown is not None:
        affected = scoped_unknown
    else:
        summary = quality.get("affected_attributes")
        affected = (
            normalize_quality_affected_attributes(summary)
            if summary or quality.get("hold_auto_eligibility")
            else set()
        )
    # 구조 오류나 범위를 확인할 수 없는 UNKNOWN은 기존과 같이 전체 보류.
    hold_all = (
        not graph or policy.get("graph_status") == "MISSING_IN_GRAPH"
        or bool(affected - SCOPED_CONDITION_FIELDS)
        or (quality.get("hold_auto_eligibility") and not affected)
        or any(isinstance(warning, dict) and warning.get("blocks_auto_eligibility") and not warning.get("affects")
               for warning in quality.get("warnings", []))
        or (structure.get("unknown_logic_groups") and scoped_unknown is None)
        or structure.get("structure_issues")
    )
    conditions = [condition.model_dump() for condition in result.conditions]
    root_index = next(
        (i for i in range(len(conditions) - 1, -1, -1)
         if conditions[i]["field"].lower() == "eligibility"),
        None,
    )
    # At this boundary `raw` is only schema-validated, not proof-validated.
    # Recheck an unaffected direct AND failure before preserving the root.
    preserve_root = (
        root_index is not None
        and not hold_all
        and scoped_unknown is not None
        and can_preserve_scoped_failed_root(
            structure, scoped_unknown, affected,
            root_status=conditions[root_index]["status"],
            has_certain_required_failure=certain_required_and_failure(
                structure, profile, affected,
            ),
            hold_all=bool(hold_all),
        )
    )
    for index, condition in enumerate(conditions):
        field = condition["field"].lower()
        # Checker가 이미 보류한 조건의 사유(원문 확인 결과·공고 확인 항목 등)는 그대로 둔다.
        if condition["status"] == "needs_check":
            continue
        # Same strict safety gate as Checker; never preserve unrelated groups.
        if preserve_root and index == root_index:
            continue
        # 보류 원인별로 사유를 다르게 적는다. 입력 누락을 데이터 오류로 안내하지 않는다.
        # 범위가 있는 경고라도 미확인 필드가 그 범위 밖이라고 단정할 수 없다.
        if hold_all or field in affected or (affected and field not in SCOPED_CONDITION_FIELDS):
            reason = QUALITY_REASON
        elif field in {"age", "region"} and profile[field] is None:
            reason = MISSING_PROFILE_REASON
        elif field == "income":
            reason = INCOME_REASON
        else:
            continue
        # 확정 상태만 보수적으로 낮추고, Checker의 비교 근거는 지우지 않고 보류 사유를 덧붙인다.
        condition["status"] = "needs_check"
        condition["reason"] = f"{condition['reason']} {reason}"

    agency = graph_policy.get("agency") or graph_policy.get("organization")
    if not agency:
        agency = next((org["name"] for org in graph.get("organizations", []) if org.get("name")), "")
    apply_url = graph_policy.get("official_apply_url")
    # 그래프 문맥이 없으면 신청 날짜 근거도 없으므로 unknown. 그 밖에는 Checker 결과를 그대로 쓴다.
    reception = result.reception_status if graph else "unknown"
    return {
        "policy_id": result.policy_id, "policy_name": policy["policy_name"],
        "conditions": conditions, "reception_status": reception, "agency": agency,
        "source_url": context["url"],
        "official_apply_url": apply_url if valid_source_url(apply_url) else None,
    }


def _citations(output: GeneratorOutput, contexts: list[dict]) -> tuple[str, list[dict]]:
    sources = {context["source_id"]: context for context in contexts}
    # 문자열로 검증해 비정상적으로 긴 숫자도 안전하게 거절한다.
    source_numbers = {str(number): number for number in sources}
    found = set(CITATION.findall(output.answer))
    if not found.issubset(source_numbers) or (output.status == "answered" and not found):
        raise AskError("LLM_OUTPUT_INVALID")
    used = sorted(source_numbers[number] for number in found)
    if set(output.used_source_ids) != set(used):
        logger.warning("LLM 출처 목록이 본문 인용과 다릅니다.")
    if output.status == "no_evidence" and used:
        raise AskError("LLM_OUTPUT_INVALID")
    renumbered = {str(number): str(index) for index, number in enumerate(used, 1)}
    answer = CITATION.sub(lambda match: f"[{renumbered[match[1]]}]", output.answer)
    citations = [
        {key: sources[number][key] for key in ("title", "url", "document_id", "page_number")}
        for number in used
    ]
    return answer, citations


def _status_correction(claim: str, check: dict) -> str | None:
    """단정(claim)이 Checker 판정과 충돌하면 확인된 상태 문구, 충돌하지 않으면 None."""
    if claim == "open":
        status = check["reception_status"]
        return None if status == "open" else RECEPTION_NOTICE.get(status, RECEPTION_NOTICE["unknown"])
    statuses = {condition["status"] for condition in check["conditions"]}
    if "ineligible" in statuses:
        return INELIGIBLE_NOTICE
    if not statuses or statuses - {"eligible"}:
        return ELIGIBILITY_NOTICE
    # 확인된 조건이 모두 eligible이어도 전체 신청 자격은 확정하지 않는다.
    return PARTIAL_ELIGIBLE_NOTICE


def _claims(clause: str) -> list[str]:
    """절에 담긴 단정 종류(open/eligible). 유보 표현이 있으면 단정이 아니다."""
    if HEDGED_CLAIM.search(clause):
        return []
    return [name for name, pattern in (("open", OPEN_CLAIM), ("eligible", ELIGIBLE_CLAIM)) if pattern.search(clause)]


def _unattributed_correction(
    clause: str, claims: list[str], by_policy: dict[str, dict], several_topics: bool,
) -> str | None:
    """자기 출처 번호가 없는 단정 절. 고친 문구, 빼야 하면 "", 문장 출처를 따르면 None.

    정책명으로 정책을 하나로 특정하면 그 정책 판정으로 고친다. 여러 정책을 나눠 설명하는 문장인데
    정책을 특정할 수 없으면, 어느 정책 판정과라도 충돌하는 단정만 뺀다(다른 정책의 출처를 빌리지 않는다).
    """
    named = _named_policy_ids(clause, list(by_policy.values()))
    if len(named) == 1:
        check = by_policy[named.pop()]
        fixes = [_status_correction(claim, check) for claim in claims]
        if all(fix is None for fix in fixes):
            return clause
        text = " ".join(dict.fromkeys(fix or OPEN_CONFIRMED for fix in fixes))
        return f"{check['policy_name']}: {text}"
    if several_topics:
        conflict = any(_status_correction(claim, check) is not None for check in by_policy.values() for claim in claims)
        return "" if conflict else clause
    return None


def _inline(text: str, later: str) -> str:
    """문장 중간 절에 넣을 문구: 뒤에 이미 붙은 출처 번호와 끝 마침표를 뺀다."""
    text = CITATION.sub(lambda match: "" if match.group() in later else match.group(), text)
    return re.sub(r"\s*[.。](?=(?:\s*\[\d+\])*\s*$)", "", text).strip()


def _clause_correction(
    clause: str, numbers: list[str], policy_of: dict[str, str], by_policy: dict[str, dict],
    whole_sentence: bool,
) -> str | None:
    """단정 절 하나를 판정에 맞게 고친 문구. 충돌이 없거나 출처를 확정할 수 없으면 None."""
    claims = _claims(clause)
    if not claims:
        return None
    named = _named_policy_ids(clause, list(by_policy.values()))
    if numbers:
        # 존재하지 않거나 비정상적인 번호가 있으면 여기서 고치지 않는다(인용 검증이 LLM 출력 오류로 처리한다).
        if any(number not in policy_of for number in numbers):
            return None
        cited = {policy_of[number] for number in numbers}
        if len(named) == 1 and named != cited:
            # 본문 대상이 명확한데 다른 정책의 출처를 붙였으면 출처 정책의 판정을 빌리지 않는다.
            check = by_policy[named.pop()]
            fixes = [_status_correction(claim, check) for claim in claims]
            text = " ".join(dict.fromkeys(fix or OPEN_CONFIRMED for fix in fixes))
            return f"{check['policy_name']}: {text}"
        if len(named) > 1 and named != cited:
            # 이름이 겹치거나 여러 정책을 지칭하는데 인용 집합이 맞지 않으면 특정 정책을 고르지 않는다.
            return RECEPTION_NOTICE["unknown"] if "open" in claims else ELIGIBILITY_NOTICE
        if not named and len(by_policy) > 1 and len(numbers) == 1 and whole_sentence:
            # 단독 단정문은 인용 하나만으로 여러 검색 정책 중 발화 대상을 확정할 수 없다.
            # 다른 설명 절이나 정책별 문장이 있는 경우는 아래 기존 절별 처리를 유지한다.
            return RECEPTION_NOTICE["unknown"] if "open" in claims else ELIGIBILITY_NOTICE
        grouped: dict[str, list[str]] = {}
        for number in numbers:
            grouped.setdefault(policy_of[number], []).append(number)
        if any(pid not in by_policy for pid in grouped):
            return None
        targets = [(by_policy[pid], "".join(f"[{n}]" for n in dict.fromkeys(nums))) for pid, nums in grouped.items()]
    else:
        targets = [(check, "") for check in by_policy.values()]
    statements, conflict = [], False
    for check, markers in targets:
        texts = []
        for claim in claims:
            fix = _status_correction(claim, check)
            conflict = conflict or fix is not None
            texts.append(fix if fix is not None else OPEN_CONFIRMED)
        statements.append((check, " ".join(dict.fromkeys(texts)), markers))
    # 인용 없는 단정은 판정 정책이 모두 충돌할 때만 바꾼다(어느 정책 이야기인지 확정할 수 없으므로).
    if not conflict or (not numbers and not all(text != OPEN_CONFIRMED for _, text, _ in statements)):
        return None
    if len(statements) == 1:
        check, text, markers = statements[0]
        named = f"{check['policy_name']}: {text}" if numbers and whole_sentence else text
        return f"{named} {markers}".strip()
    return " ".join(f"{check['policy_name']}: {text} {markers}".strip() for check, text, markers in statements)


_INTERNAL_STATUS = re.compile(
    r"(?<![A-Za-z0-9_/.-])(?:needs_check|reception_status)(?![A-Za-z0-9_/-])"
    r"|(?<![A-Za-z0-9_/.-])(?:open|unknown|closed|before)(?![A-Za-z0-9_/-])"
    r"|\(\s*(?:open|unknown|closed|before)\s*\)"
    r"|\*\*\s*(?:open|unknown|closed|before)\s*\*\*"
)
# 대소문자를 구분한다(re.IGNORECASE 미사용): 내부 코드값은 항상 소문자로 저장되므로
# (OPEN_CONFIRMED 등), "Open Innovation"처럼 영문 고유명사가 대문자로 시작하는 경우는
# 걸러지지 않는다. "접수 상태" 같은 문맥 단어 없이도 소문자 토큰 자체를 바로 잡아낸다.

_REDUNDANT_STATUS_TAG = re.compile(
    r"(마감)\s*\(\s*closed\s*\)|(보류)\s*\(\s*needs_check\s*\)",
    re.IGNORECASE,
)


def _remove_redundant_status_tags(text: str) -> str:
    return _REDUNDANT_STATUS_TAG.sub(lambda match: match[1] or match[2], text)


def _has_internal_status(text: str) -> bool:
    return _INTERNAL_STATUS.search(text) is not None


def _align_answer_with_checks(answer: str, contexts: list[dict], checks: list[dict]) -> tuple[str, bool]:
    """접수 상태·자격은 Checker 판정을 기준으로 안내한다.

    인용 번호로 절이 가리키는 정책을 찾고, 그 정책의 판정과 충돌하는 단정 절만 확인된 상태 문구로 바꾼다.
    여러 정책을 함께 인용하면 정책마다 자기 출처 번호를 붙여 따로 안내한다. 유보 절은 그대로 둔다.
    인용 번호는 문자열로만 다룬다(비정상적으로 긴 번호도 예외 없이 인용 검증으로 넘긴다).
    """
    policy_of = {str(context["source_id"]): context["policy_id"] for context in contexts}
    by_policy = {check["policy_id"]: check for check in checks}
    parts = SENTENCE_BREAK.split(answer)
    changed = False
    for index in range(0, len(parts), 2):
        sentence = parts[index]
        if not sentence.strip():
            continue
        clauses = CLAUSE_BREAK.split(sentence)
        sentence_numbers = list(dict.fromkeys(CITATION.findall(sentence)))
        last = len(clauses) - 1
        # 앞 절이 모두 자기 출처를 달았다면 끝 출처는 마지막 절 자신의 출처다.
        shared = any(not CITATION.search(clauses[position]) for position in range(0, last, 2))
        tail = SENTENCE_TAIL.search(clauses[last]).group()
        topics = {match.group(1) for match in map(TOPIC.match, clauses[::2]) if match}
        removed = []
        for position in range(0, len(clauses), 2):
            clause = clauses[position]
            later = "".join(clauses[position + 1:])
            body = clause[: len(clause) - len(tail)] if position == last else clause
            claims = _claims(clause)
            # 여러 절 문장에서 자기 출처 번호가 없는 단정 절은 문장 끝 출처를 바로 빌리지 않는다.
            if last and claims and not CITATION.search(body if shared else clause):
                result = _unattributed_correction(body, claims, by_policy, len(topics) > 1)
                if result == "":
                    removed.append(position)
                    changed = True
                    continue
                if result is not None:
                    if result != body:
                        clauses[position] = _inline(result, later) + (tail if position == last else "")
                        changed = True
                    continue
            numbers = list(dict.fromkeys(CITATION.findall(clause))) or sentence_numbers
            fixed = _clause_correction(clause, numbers, policy_of, by_policy, last == 0)
            if fixed is not None:
                clauses[position] = fixed if position == last else _inline(fixed, later)
                changed = True
        keep = [position for position in range(0, len(clauses), 2) if position not in removed]
        if not removed:
            parts[index] = "".join(clauses)
        elif not keep:
            # 모든 절이 빠지면 출처를 잃지 않도록 확인 필요 안내로 대신한다.
            kinds = dict.fromkeys(kind for position in removed for kind in _claims(clauses[position]))
            notice = " ".join(RECEPTION_NOTICE["unknown"] if kind == "open" else ELIGIBILITY_NOTICE for kind in kinds)
            parts[index] = _inline(notice, "") + tail
        else:
            rebuilt = "".join(clauses[position] + (clauses[position + 1] if position != keep[-1] else "")
                              for position in keep)
            parts[index] = rebuilt if last in keep else rebuilt.rstrip() + tail
    return "".join(parts), changed


def _is_closed(policy: dict, raw: dict) -> bool:
    """Checker가 유효한 형식으로 마감(closed)을 판정한 정책만 후순위 대상이다.

    형식 오류는 여기서 숨기지 않는다. 해당 정책이 Context에 채택되면 _policy_check가 오류로 처리한다.
    """
    if not policy.get("graph"):
        return False
    try:
        result = EligibilityOutput.model_validate(raw)
    except ValidationError:
        return False
    return result.policy_id == policy["policy_id"] and result.reception_status == CLOSED


def _named_policy_ids(question: str, policies: list[dict]) -> set[str]:
    """질문에 정책명 전체가 들어 있는 정책 ID. 부분 이름·짧은 이름은 인정하지 않는다."""
    asked = NAME_NOISE.sub("", question).lower()
    named = set()
    for policy in policies:
        name = policy.get("policy_name")
        key = NAME_NOISE.sub("", name).lower() if isinstance(name, str) else ""
        if len(key) >= MIN_NAMED_POLICY_LENGTH and key in asked:
            named.add(policy["policy_id"])
    return named


def _display_check(check: dict, display_fn: Callable[[list[dict]], list[dict]]) -> dict:
    """화면용 조건 요약. 원본 행만 고를 수 있고 필드·상태 조합을 바꿀 수 없다."""
    shown = display_fn([dict(condition) for condition in check["conditions"]])
    original = check["conditions"]
    if (
        not isinstance(shown, list) or not shown
        or any(item not in original for item in shown)
        or {(c["field"], c["status"]) for c in shown} != {(c["field"], c["status"]) for c in original}
    ):
        raise AskError("ELIGIBILITY_OUTPUT_INVALID")
    return {**check, "conditions": shown}


def run_ask_pipeline(
    request: AskRequest,
    *,
    search_fn: Callable[..., dict],
    check_fn: Callable[[dict, dict], dict],
    generate_fn: Callable[..., dict],
    display_fn: Callable[[list[dict]], list[dict]] | None = None,
) -> AskResponse:
    """실제 함수와 테스트 대역에 동일한 호출 계약을 적용한다.

    generate_fn(question, contexts, policy_checks, history, profile=정규화 프로필)
    display_fn: 정책 한 건의 conditions를 화면용으로 요약한다(선택). LLM에는 요약 전 조건을 전달한다.
    """
    profile = request.profile.model_dump()
    supplied_region = profile["region"].strip()
    canonical = ALIASES.get(supplied_region, supplied_region)
    profile["region"] = canonical if canonical in SIDOS else None
    unknown_region = bool(supplied_region and profile["region"] is None)
    if unknown_region:
        logger.info("입력 지역을 시·도 이름으로 확인하지 못했습니다.")
    notice = {"kind": "warn", "title": "추가 확인 필요",
              "text": "입력 지역을 확인하지 못했습니다. 시·도 이름을 확인해 주세요."} if unknown_region else None
    filters = {"region": profile["region"]} if profile["region"] else None
    if request.selected_policy_id:
        # 사용자가 고른 정책은 지역 필터로 빼지 않는다. 지역이 맞지 않으면 Checker가 지역 조건으로 판정한다.
        filters = {"policy_id": request.selected_policy_id}
    policies = search_fn(request.question, filters=filters, k=10)["policies"]
    if request.selected_policy_id:
        # 검색 어댑터가 필터를 무시해도 다른 정책으로 질문을 바꾸지 않는다.
        policies = [policy for policy in policies if policy["policy_id"] == request.selected_policy_id]
    # 인용 가능한 근거 후보를 한 번만 만들고, 판정 대상 선정과 최종 근거 선택에 재사용한다.
    ranked = rank_citable_contexts(policies)
    citable = {context["policy_id"] for context in ranked}
    # 인용 가능한 원문이 있는 정책만 판정 후보다. 원문이 없으면 Checker와 LLM을 호출하지 않는다.
    if not citable:
        return AskResponse(status="no_evidence", answer="인용할 수 있는 공식 원문을 찾지 못했습니다.", notice=notice)

    by_id = {policy["policy_id"]: policy for policy in policies}
    # 후보마다 한 번만 판정하고, Context에 채택된 정책은 이 결과를 재사용한다.
    raw_checks = {pid: check_fn(profile, by_id[pid]) for pid in by_id if pid in citable}
    closed = {pid for pid, raw in raw_checks.items() if _is_closed(by_id[pid], raw)}
    # 사용자가 정책명을 직접 물은 마감 정책은 후순위에서 빼서 근거 자리를 확보한다(마감 여부는 그대로 응답).
    named = _named_policy_ids(request.question, [by_id[pid] for pid in citable])
    contexts = select_contexts(ranked, deprioritized_policy_ids=closed - named)

    contexts_by_policy = {}
    for context in contexts:
        contexts_by_policy.setdefault(context["policy_id"], []).append(context)
    selected_ids = {context["chunk_id"] for context in contexts}
    candidates_by_policy = {}
    for context in ranked:
        pid = context["policy_id"]
        if pid in contexts_by_policy and context["chunk_id"] not in selected_ids:
            candidates_by_policy.setdefault(pid, []).append(context)
    checks = []
    for pid, selected in contexts_by_policy.items():
        check = _policy_check(by_id[pid], raw_checks[pid], profile, selected[0])
        if request.selected_policy_id:
            _hold_selected_policy_region(by_id[pid], check, profile["region"])
        contacts = _collect_contacts(selected)
        if not contacts:
            # 미채택 후보 청크에서만 찾았다 — 그 청크는 답변의 citations에 들어가지 않으므로,
            # 화면이 "문의처 원문"으로 인용되지 않은 출처를 링크하지 않도록 출처 정보는 비운다.
            contacts = [
                contact.model_copy(update={"document_id": None, "chunk_id": None, "source_url": None})
                for contact in _collect_contacts(candidates_by_policy.get(pid, ()))
            ]
        check["contacts"] = contacts or None
        check["application_schedule"] = application_schedule(by_id[pid], check["reception_status"])
        check.update(application_details(selected))
        checks.append(check)
    history = [turn.model_dump() for turn in request.history[-10:]]
    try:
        raw = generate_fn(request.question, contexts, checks, history, profile=dict(profile))
    except JSONDecodeError as exc:
        raise AskError("LLM_OUTPUT_INVALID") from exc
    except (TimeoutError, TimeoutException) as exc:
        raise AskError("LLM_TIMEOUT") from exc
    except (ConnectionError, NetworkError) as exc:
        raise AskError("LLM_UNAVAILABLE") from exc
    except Exception as exc:
        # 검색이 데이터셋을 선택하기 전에 generator의 load_dotenv()를 실행하지 않는다.
        from backend.llm.generator import LLMOutputError, LLMUnavailableError

        if isinstance(exc, LLMOutputError):
            raise AskError("LLM_OUTPUT_INVALID") from exc
        if isinstance(exc, LLMUnavailableError):
            raise AskError("LLM_UNAVAILABLE") from exc
        raise
    try:
        output = GeneratorOutput.model_validate(raw)
    except ValidationError as exc:
        raise AskError("LLM_OUTPUT_INVALID") from exc
    output = output.model_copy(update={"answer": _remove_redundant_status_tags(output.answer)})
    aligned = False
    if output.status != "no_evidence":
        fixed, aligned = _align_answer_with_checks(output.answer, contexts, checks)
        if aligned:
            # 내용은 로그에 남기지 않는다.
            logger.warning("생성 본문의 접수·자격 안내를 Checker 판정에 맞춰 바꿨습니다.")
            # 교정으로 유일한 인용이 빠지면 answered의 필수 인용 계약을 지키기 위해 info로 낮춘다.
            remaining = set(CITATION.findall(fixed))
            output = output.model_copy(update={
                "answer": fixed,
                "status": "info" if output.status == "answered" and not remaining else output.status,
                "used_source_ids": [context["source_id"] for context in contexts
                                    if str(context["source_id"]) in remaining],
            })
    follow_ups = [
        item for item in output.follow_up_questions
        if item.kind != "ask_info" or profile.get(item.field) is None
    ]
    # 상태가 섞인 자유 문장을 추측으로 고치면 정책과 출처의 연결이 바뀔 수 있다.
    if any(_has_internal_status(text) for text in (output.answer, *(item.text for item in follow_ups))):
        raise AskError("LLM_OUTPUT_INVALID")
    answer, citations = _citations(output, contexts)
    if output.status == "no_evidence":
        checks = []
    if notice is None and any(c["status"] == "needs_check" for p in checks for c in p["conditions"]):
        notice = {"kind": "warn", "title": "추가 확인 필요", "text": HOLD_NOTICE_TEXT}
    if notice is None and aligned:
        notice = {"kind": "warn", "title": "추가 확인 필요",
                  "text": "접수 상태와 신청 자격은 판정 결과를 기준으로 안내했습니다. 공식 공고를 확인해 주세요."}
    if display_fn is not None:
        checks = [_display_check(check, display_fn) for check in checks]
    return AskResponse(
        status=output.status, answer=answer, citations=citations, policy_checks=checks,
        follow_up_questions=follow_ups, notice=notice,
    )
