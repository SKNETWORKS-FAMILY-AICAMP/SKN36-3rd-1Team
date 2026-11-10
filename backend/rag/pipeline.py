"""정책 검색, 조건 검사, 답변 생성과 출처 검증을 연결한다."""

from collections.abc import Callable
from json import JSONDecodeError
import logging
import re

from httpx import NetworkError, TimeoutException
from pydantic import ValidationError

from backend.api.schemas import AskRequest, AskResponse, EligibilityOutput, GeneratorOutput
from backend.rag.context_builder import build_contexts, valid_source_url
from database.region_search import ALIASES, SIDOS

logger = logging.getLogger(__name__)
CITATION = re.compile(r"\[(\d+)\]")
# database/hybrid_search.py가 data_quality.affected_attributes로 내보내는 필드 목록과 같다("all" 제외).
# 목록 밖의 값과 "all"은 영향 범위를 확정할 수 없으므로 정책 전체를 보류한다.
# TODO(#23 병합 후): 검색 담당이 공통 상수를 제공하면 그 상수로 교체한다.
SCOPED_CONDITION_FIELDS = {"age", "region", "income", "housing", "employment", "exclusion", "marriage", "children"}
QUALITY_REASON = "데이터가 불확실하여 공식 공고에서 조건을 확인해야 합니다."
MISSING_PROFILE_REASON = "입력한 정보가 없어 자동 판정을 보류했습니다. 정보를 입력하거나 공식 공고에서 확인해 주세요."
INCOME_REASON = "소득 기준(개인·가구 구분)이 확정되지 않아 공식 공고에서 확인해야 합니다."
# 신청 상태(before/open/closed/unknown)는 Checker 단일 책임이다. Pipeline은 날짜를 판정하지 않는다.
CLOSED = "closed"


class AskError(RuntimeError):
    """API가 사용자에게 전달할 오류 코드."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


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
    affected = set(quality.get("affected_attributes") or [])
    # 검색이 반환하는 그룹 ID와 구조 오류만으로는 영향받는 필드를 확정할 수 없다.
    hold_all = (
        not graph or policy.get("graph_status") == "MISSING_IN_GRAPH"
        or bool(affected - SCOPED_CONDITION_FIELDS)
        or (quality.get("hold_auto_eligibility") and not affected)
        or any(isinstance(warning, dict) and warning.get("blocks_auto_eligibility") and not warning.get("affects")
               for warning in quality.get("warnings", []))
        or structure.get("unknown_logic_groups") or structure.get("structure_issues")
    )
    conditions = [condition.model_dump() for condition in result.conditions]
    for condition in conditions:
        field = condition["field"].lower()
        # Checker가 이미 보류한 조건의 사유(원문 확인 결과·공고 확인 항목 등)는 그대로 둔다.
        if condition["status"] == "needs_check":
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
    policies = search_fn(request.question, filters=filters, k=10)["policies"]
    # 인용 가능한 원문이 있는 정책만 판정 후보다. 원문이 없으면 Checker와 LLM을 호출하지 않는다.
    citable = {context["policy_id"] for context in build_contexts(policies, max_contexts=len(policies) * 100)}
    if not citable:
        return AskResponse(status="no_evidence", answer="인용할 수 있는 공식 원문을 찾지 못했습니다.", notice=notice)

    by_id = {policy["policy_id"]: policy for policy in policies}
    # 후보마다 한 번만 판정하고, Context에 채택된 정책은 이 결과를 재사용한다.
    raw_checks = {pid: check_fn(profile, by_id[pid]) for pid in by_id if pid in citable}
    closed = {pid for pid, raw in raw_checks.items() if _is_closed(by_id[pid], raw)}
    contexts = build_contexts(policies, deprioritized_policy_ids=closed)

    first_context = {}
    for context in contexts:
        first_context.setdefault(context["policy_id"], context)
    checks = [
        _policy_check(by_id[pid], raw_checks[pid], profile, context)
        for pid, context in first_context.items()
    ]
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
    answer, citations = _citations(output, contexts)
    follow_ups = [
        item for item in output.follow_up_questions
        if item.kind != "ask_info" or profile.get(item.field) is None
    ]
    if output.status == "no_evidence":
        checks = []
    if notice is None and any(c["status"] == "needs_check" for p in checks for c in p["conditions"]):
        notice = {"kind": "warn", "title": "추가 확인 필요",
                  "text": "자동 판정을 보류한 조건이 있습니다. 공식 공고를 확인해 주세요."}
    if display_fn is not None:
        checks = [_display_check(check, display_fn) for check in checks]
    return AskResponse(
        status=output.status, answer=answer, citations=citations, policy_checks=checks,
        follow_up_questions=follow_ups, notice=notice,
    )
