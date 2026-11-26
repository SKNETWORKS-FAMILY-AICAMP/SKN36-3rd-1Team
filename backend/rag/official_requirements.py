"""정책의 공식 자격기준(#76)을 이번 응답의 근거 원문에서만 확인해 `official_requirements`로 만든다.

두 경로가 있고, 둘 다 이번 응답에서 선택된 **같은 정책**의 근거 청크(contexts)만 쓴다.

1. 검수 기록(review record): 사람이 값·단위·연도를 검수한 기록이 있고 그 값이 근거 원문에 그대로 있을 때.
   기록 저장 위치·작성 절차는 미확정이라 API 기본값은 빈 목록이다(create_app의 requirement_reviews).
2. 원문 항목 발췌: 자격 블록([지원대상]·[선정기준]·[지원대상 상세]·[추가 신청자격조건] 등) 안에서 원문이 직접
   "항목: 값" 또는 "(항목) 값"으로 이름 붙인 줄만, 허용된 자격 항목(연령·소득·거주·가구·취업·혼인·학력·대상)일 때
   원문 그대로 낸다. label은 원문의 항목명, value는 그 줄의 원문 값이다. 항목명 없이 바로 이어지는 줄(예: "※ 단,
   … 중복수혜 불가")은 그 항목의 단서이므로 값에 원문 그대로 이어 붙인다(단서를 빼고 앞줄만 내보내지 않는다).
   자동 추출 항목은 검수 완료 기준이 아니다(docs/api/ask_contract.md 「#76 공식 자격기준」 절).

공통 원칙(fail closed):
- 값을 만들거나 계산·환산하지 않는다(중위소득 비율→원화 환산, 기준연도 추정 없음). 추가 검색·LLM 호출도 없다.
- 지원내용·신청기간·신청방법·구비서류·근거법령·참여 제한(제외) 블록은 보지 않는다. 지원 금액을 소득 기준으로,
  신청기간을 자격기준으로 옮기지 않기 위해서다.
- 우대·가점·우선 선정·제외 문구가 섞인 값, 이용료·본인부담·무료 등 비용 산정 문구가 섞인 값(신청 자격이 아니라
  이용료 구간일 수 있다), 수집기가 만든 기본값·표기(제한없음·무관·미상·0~0세·0원 ~ 0원 등), 청크 끝에서 잘렸을 수
  있는 항목은 내보내지 않는다. 단서까지 이어 붙인 값 전체에 같은 규칙을 적용하고, 걸리면 항목 전체를 뺀다.
  기준이 없다는 뜻의 값으로 null을 대신 채우지 않는다.
- 데이터 품질 경고 중 원문 자체의 표기 오류(SOURCE_AGE_ERROR_MARK)가 있는 항목은 검수 기록이 있어도 내보내지 않는다.
  그래프·Checker 쪽 경고(자동 판정 보류)는 원문 기준의 부재가 아니므로 원문 발췌를 막지 않는다.
- 검색 계층의 evidence_document_check가 MISMATCH인 문서(그래프상 그 정책의 근거 문서가 아님)의 청크는 쓰지 않는다.
- 검수 값은 숫자 경계까지 맞아야 한다(원문 '150만원'에 검수 값 '50만원'은 불일치). 형식이 잘못된 기록은 건너뛴다.
- 이 값은 정책이 요구하는 기준의 근거일 뿐이며 사용자의 충족 여부(policy_checks.conditions)가 아니다.

검수 기록 한 건의 형식(데이터 검수 담당과 합의 전 제안):
    {"policy_id": "POL_...", "label": "소득 기준", "value": "원문 그대로의 기준 문장",
     "document_id": "DOC_...", "chunk_id": "...", "review_status": "verified",
     "checks": {"value": "verified", "unit": "verified" | "not_applicable",
                "year": "verified" | "not_applicable"}}
"""

from collections.abc import Iterable
import logging
import re

from backend.rag.source_sections import BLOCK

logger = logging.getLogger(__name__)

REVIEW_VERIFIED = "verified"
# 값은 반드시 대조해야 한다. 단위·연도는 원문에 없을 수 있으므로 '해당 없음'을 명시한 경우만 허용한다.
REQUIRED_CHECKS = {"value": frozenset({"verified"}),
                   "unit": frozenset({"verified", "not_applicable"}),
                   "year": frozenset({"verified", "not_applicable"})}
_SPACE = re.compile(r"\s+")

# 공식 원문 필드를 그대로 담는 자격 블록만 읽는다.
# [지원대상 및 자격요건]은 온통청년 수집기가 코드값으로 만든 문장(기본값 '제한없음', '0원 ~ 0원', '제한여부')이라 제외한다.
# 정부24 [구조화 지원조건 요약]도 수집기가 만든 요약이라 제외한다. [참여 제한대상]은 제외 조건(#78)이다.
REQUIREMENT_BLOCKS = frozenset({
    "지원대상", "선정기준", "지원대상 및 선정기준", "지원대상 상세", "추가 신청자격조건",
})
# 원문 항목명(공백 제거) → 자격 항목 분류. 목록 밖의 항목(지원금액·지원기간·사업내용·특화요건 등)은 내보내지 않는다.
REQUIREMENT_KEYS = {
    **dict.fromkeys(("연령", "나이", "대상연령", "가입연령", "신청연령", "연령기준"), "age"),
    **dict.fromkeys(("소득", "소득요건", "소득기준", "소득수준", "가구소득", "소득인정액", "근로·사업소득"), "income"),
    **dict.fromkeys(("거주", "거주지", "거주지역", "거주요건"), "region"),
    **dict.fromkeys(("가구요건", "가구조건", "가구기준"), "household"),
    **dict.fromkeys(("취업상태", "취업요건", "고용상태"), "employment"),
    **dict.fromkeys(("혼인상태", "혼인요건"), "marriage"),
    **dict.fromkeys(("학력요건", "전공요건"), "education"),
    **dict.fromkeys(("대상", "지원대상", "자격기준", "자격요건", "신청자격"), "target"),
}
_BULLET = r"[-–·•∙ㅇ○◦▪■□◆◇]*\s*"
_KEYED_LINE = (
    re.compile(rf"^{_BULLET}\(\s*([^()\d:：]{{1,12}}?)\s*\)\s*(\S.*)$"),  # ㅇ (소득) 기준 중위소득 100% 이하
    re.compile(rf"^{_BULLET}([^()\d:：]{{1,12}}?)\s*[:：]\s*(\S.*)$"),      # - 연령: 만 15세 ~ 69세
)
# 필수 자격과 다른 뜻(우대·가점·우선·제외)이 섞이거나 자격이 아닌 정보(신청·접수 기간)가 섞인 값.
_OTHER_MEANING = re.compile(r"우대|가점|우선|제외|신청\s*기간|접수\s*기간")
# 뒤 줄을 가리키는 값("아래 모든 조건을 충족하는 자")은 그 줄만으로 기준이 아니다.
_REFERS_ELSEWHERE = re.compile(r"아래|다음\s*(?:과|와|의|중|각|에)")
# 신청 자격이 아니라 이용료·본인부담 구간을 정하는 문구("120% 이하 무료, 초과 시 40% 본인부담").
# '차상위본인부담'은 차상위 확인 사업 이름이라 비용 문구로 보지 않는다. '소득별 차등지원'은 지원액 차이일 뿐
# 앞의 소득 상한은 신청 자격이므로 막지 않는다.
_FEE_MEANING = re.compile(r"(?<!차상위)본인\s*부담|자부담|자기\s*부담|이용료|이용\s*요금|무료|감면|할인")
# 수집 단계의 기본값·표기 오류. '기준 없음'으로 오해되거나 원문 값이 아니다.
_PLACEHOLDER_VALUES = frozenset({"제한없음", "무관", "해당없음", "없음", "-", "미확인", "미상", "공고문참조", "공고참조",
                                 "별도공고", "공고문확인"})
_COLLECTOR_MARK = re.compile(r"미상|표기\s*오류|0\s*[~∼〜]\s*0\s*세|0원\s*[~∼〜]\s*0원|제한여부\s*[:：]")
# 원문 자체의 표기 오류를 뜻하는 품질 경고 → 그 항목은 원문 발췌도 내보내지 않는다.
SOURCE_TEXT_ERROR_CODES = {"SOURCE_AGE_ERROR_MARK": "age"}
# 검수 기록의 label(자유 표기)을 분류할 때 REQUIREMENT_KEYS에 없으면 쓰는 보조 규칙.
_LABEL_CATEGORY = ((re.compile(r"연령|나이"), "age"), (re.compile(r"소득"), "income"))
DOCUMENT_CHECK_MISMATCH = "MISMATCH"


def _normalized(text: str) -> str:
    """의미를 바꾸지 않는 공백·줄바꿈 차이만 없앤다. 숫자·단위·기호는 그대로다."""
    return _SPACE.sub(" ", text).strip()


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _is_verified(review: dict) -> bool:
    checks = review.get("checks")
    # 형식이 잘못된 값(배열·객체 등)은 검수 완료로 보지 않는다(해시 불가 값으로 예외가 나지 않게 문자열만 비교).
    return (
        review.get("review_status") == REVIEW_VERIFIED
        and isinstance(checks, dict)
        and all(isinstance(checks.get(name), str) and checks[name] in allowed
                for name, allowed in REQUIRED_CHECKS.items())
    )


def _category(label: str) -> str | None:
    compact = _SPACE.sub("", label)
    if compact in REQUIREMENT_KEYS:
        return REQUIREMENT_KEYS[compact]
    return next((category for pattern, category in _LABEL_CATEGORY if pattern.search(compact)), None)


def _contains_value(text: str, value: str) -> bool:
    """원문에 값이 그대로 있는지 본다. 숫자로 시작·끝나는 값은 앞뒤가 다른 숫자에 이어지면 불일치다.

    예: 원문 '150만원'·'1,500만원'에 값 '50만원'은 불일치, '만 18세 미만'에 '18세 미만'은 일치.
    """
    text, value = _normalized(text), _normalized(value)
    start = text.find(value)
    while start >= 0:
        before = text[start - 1] if start else ""
        after = text[start + len(value):start + len(value) + 1]
        joined_before = value[0].isdigit() and before != "" and (before.isdigit() or before in ",.")
        joined_after = value[-1].isdigit() and after != "" and (after.isdigit() or after in ",.")
        if not (joined_before or joined_after):
            return True
        start = text.find(value, start + 1)
    return False


def _mismatched_documents(document_checks: object) -> set[str]:
    if not isinstance(document_checks, dict):
        return set()
    return {document_id for document_id, status in document_checks.items() if status == DOCUMENT_CHECK_MISMATCH}


def _own_contexts(policy_id: str, contexts: list[dict], excluded_documents: set[str] = frozenset()) -> list[dict]:
    """같은 정책이고 문서·청크·본문이 있는 근거만, 선택된 순서(source_id 순서) 그대로 남긴다.

    그래프상 이 정책의 근거 문서가 아닌 문서(evidence_document_check=MISMATCH)의 청크는 뺀다.
    """
    return [context for context in contexts
            if isinstance(context, dict) and context.get("policy_id") == policy_id
            and _text(context.get("document_id")) and _text(context.get("chunk_id"))
            and isinstance(context.get("text"), str)
            and context["document_id"] not in excluded_documents]


def _reviewed_requirements(policy_id: str, contexts: list[dict], reviews: Iterable[dict] | None,
                           blocked_categories: set[str] = frozenset()) -> list[dict]:
    """검수 기록 중 같은 정책·같은 근거 청크·원문 값이 모두 맞는 것만 남긴다.

    원문 표기 오류 경고가 있는 분류(예: SOURCE_AGE_ERROR_MARK → 연령)의 기록은 쓰지 않는다.

    같은 항목(label)에 서로 다른 값이 확정되면 하나를 고르지 않고 그 항목 전체를 내보내지 않는다.
    """
    if not reviews:
        return []
    order = {}
    for index, context in enumerate(contexts):
        order.setdefault(context["chunk_id"], (index, context))

    accepted = []
    for position, review in enumerate(reviews):
        if not isinstance(review, dict) or review.get("policy_id") != policy_id or not _is_verified(review):
            continue
        label, value = _text(review.get("label")), _text(review.get("value"))
        document_id, chunk_id = _text(review.get("document_id")), _text(review.get("chunk_id"))
        found = order.get(chunk_id) if chunk_id else None
        if not label or not value or not document_id or found is None or _category(label) in blocked_categories:
            continue
        index, context = found
        if context.get("document_id") != document_id or not _contains_value(context["text"], value):
            # 내용은 로그에 남기지 않는다.
            logger.info("근거 원문과 맞지 않는 공식 기준 검수 기록을 제외했습니다.")
            continue
        accepted.append((index, position, label, value, document_id, chunk_id))

    values_by_label = {}
    for _, _, label, value, *_ in accepted:
        values_by_label.setdefault(label, set()).add(_normalized(value))
    conflicted = {label for label, values in values_by_label.items() if len(values) > 1}
    if conflicted:
        logger.info("값이 서로 다른 공식 기준 항목 %d개를 확정하지 않았습니다.", len(conflicted))
    return [{"label": label, "value": value, "document_id": document_id, "chunk_id": chunk_id}
            for _, _, label, value, document_id, chunk_id in sorted(accepted, key=lambda item: item[:2])
            if label not in conflicted]


def _keyed_value(line: str) -> tuple[str, str, str] | None:
    """원문이 직접 항목명을 붙인 줄이면 (원문 항목명, 분류, 원문 값)을 돌려준다."""
    for pattern in _KEYED_LINE:
        match = pattern.match(line)
        if not match:
            continue
        label, value = match[1].strip(), match[2].strip()
        category = REQUIREMENT_KEYS.get(_SPACE.sub("", label))
        return (label, category, value) if category is not None else None
    return None


def _is_keyed(line: str) -> bool:
    """항목명이 붙은 줄인가(허용 항목인지는 보지 않는다). 다음 항목의 시작을 가려내는 데 쓴다."""
    return any(pattern.match(line) for pattern in _KEYED_LINE)


def _acceptable(value: str) -> bool:
    if any(pattern.search(value)
           for pattern in (_OTHER_MEANING, _FEE_MEANING, _COLLECTOR_MARK, _REFERS_ELSEWHERE)):
        return False
    return _SPACE.sub("", value).rstrip(".") not in _PLACEHOLDER_VALUES


def _source_requirements(contexts: list[dict], blocked_categories: set[str]) -> list[dict]:
    rows = []
    for context in contexts:
        text = context["text"]
        for block in BLOCK.finditer(text):
            if block[1].strip() not in REQUIREMENT_BLOCKS:
                continue
            lines = [line.strip() for line in block[2].splitlines() if line.strip()]
            # 블록이 청크 끝까지 이어지면 마지막 줄(과 그 줄이 속한 항목)은 다음 청크로 잘렸을 수 있다.
            truncated = block.end() >= len(text.rstrip())
            for start, line in enumerate(lines):
                found = _keyed_value(line)
                if found is None or found[1] in blocked_categories:
                    continue
                end = start + 1
                while end < len(lines) and not _is_keyed(lines[end]):
                    end += 1
                if truncated and end == len(lines):
                    continue
                # 항목명 없는 다음 줄은 이 항목의 단서다. 원문 줄을 공백 하나로 이어 그대로 담는다.
                value = " ".join([found[2], *lines[start + 1:end]])
                if not _acceptable(value):
                    continue
                rows.append({"label": found[0], "value": value,
                             "document_id": context["document_id"], "chunk_id": context["chunk_id"]})
    return rows


def _blocked_categories(data_quality: dict | None) -> set[str]:
    warnings = data_quality.get("warnings") if isinstance(data_quality, dict) else None
    if not isinstance(warnings, list):
        return set()
    return {SOURCE_TEXT_ERROR_CODES[warning["code"]] for warning in warnings
            if isinstance(warning, dict) and isinstance(warning.get("code"), str)
            and warning["code"] in SOURCE_TEXT_ERROR_CODES}


def official_requirements(
    policy_id: str,
    contexts: list[dict],
    reviews: Iterable[dict] | None = (),
    data_quality: dict | None = None,
    document_checks: dict | None = None,
) -> list[dict] | None:
    """한 정책의 선택된 근거(contexts)로 `official_requirements`를 만든다. 결과가 없으면 None(계약: 값 없음은 null).

    - 다른 정책의 근거·기록, 선택되지 않은 청크는 쓰지 않는다.
    - 검수 기록이 있는 항목(label)은 검수 값만 쓰고, 같은 항목의 원문 발췌는 덧붙이지 않는다.
    - 같은 항목·같은 값은 근거 순서상 첫 번째만 남긴다.
    - document_checks는 검색 계층의 evidence_document_check({document_id: PASS|MISMATCH|NOT_TESTED})다.
    """
    own = _own_contexts(policy_id, contexts or [], _mismatched_documents(document_checks))
    if not own:
        return None
    blocked = _blocked_categories(data_quality)
    reviewed = _reviewed_requirements(policy_id, own, reviews, blocked)
    reviewed_labels = {row["label"] for row in reviewed}
    extracted = [row for row in _source_requirements(own, blocked)
                 if row["label"] not in reviewed_labels]
    rows, seen = [], set()
    for row in reviewed + extracted:
        key = (_SPACE.sub("", row["label"]), _normalized(row["value"]))
        if key not in seen:
            seen.add(key)
            rows.append(row)
    return rows or None
