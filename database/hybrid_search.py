"""
Hybrid 검색 인터페이스 — Qdrant(정책 원문 근거) + Neo4j(정책 관계·자격 조건 구조)를 함께 조회한다.

흐름 (search_policy_context)
  ① 질문 → 임베딩 벡터 (embedding.make_ollama_embedder, Ollama)
  ② Qdrant Top-K 검색 (qdrant.store.search_chunks) → 근거 청크 + policy_id
  ③ policy_id로 Neo4j 정책 문맥 조회 (파라미터화된 읽기 전용 Cypher)
  ④ policy_id 기준으로 결합 → 정책별 {원문 근거 evidence, 그래프 문맥 graph}

지키는 규칙
- 두 DB 모두 "읽기"만 한다. 이 모듈에는 쓰기·삭제 함수가 없다. Neo4j는 읽기 전용 세션(Neo4jDriverRunner.read_rows)만 쓴다.
- 사용자 입력으로 Cypher·Qdrant 필터 문자열을 만들지 않는다.
  · Qdrant 필터: 허용 필드(ALLOWED_FILTER_FIELDS)의 "값"만 받아 store._to_filter가 객체로 만든다.
  · Cypher: 쿼리 문장은 모듈 상수이고, 정책·문서 ID는 $파라미터로만 넘긴다.
- 검색 점수(score)는 "질문과 청크의 의미 유사도"일 뿐 자격 충족 여부가 아니다.
  이 모듈은 자격을 판정하지 않는다 (eligibility.evaluated = False). 조건 그룹의 AND/OR/NOT/UNKNOWN 구조는 그대로 돌려준다.
- Neo4j에 없는 policy_id는 만들어 내지 않고 MISSING_IN_GRAPH로 표시한다.

반환값은 모두 dict/list (JSON으로 바로 직렬화 가능). RAG 담당 코드에서 그대로 프롬프트 문맥으로 쓸 수 있게 했다.
"""

import os
import re
import time
from collections import Counter
from contextlib import contextmanager
from typing import Any, Callable, Iterator

import requests

from database import data_quality_flags as dqf
from database import embedding as em
from database import full_index as fi
from database import handoff_data as hd
from database import retrieval_run as rr
from database import region_search as rs
from database.chunking import make_point_id
from database.neo4j import load_graph as lg
from database.qdrant import store

# ---------------------------------------------------------------------------
# 1. 설정값
# ---------------------------------------------------------------------------
# 기존 107 point 실험 컬렉션 (문서 5개). 호환용: collection을 직접 지정하는 호출·교차 검증 기본값에만 쓴다.
LEGACY_EXPERIMENT_COLLECTION = "exp_b2_qwen3emb06b_v1"
DEFAULT_COLLECTION = LEGACY_EXPERIMENT_COLLECTION   # 이름 호환용 별칭 (검색 기본값은 DEFAULT_PROFILE)

# ---- 검색 프로필 (2026-11-06) ----
# 프로필 하나가 컬렉션·임베딩 모델·차원·질의 지시문을 함께 정한다. 값은 색인 구성(full_index)과
# 평가 실행기(retrieval_run.QUERY_TEMPLATES)에서 그대로 가져온다 → 실험에서 검증한 설정과 어긋날 수 없다.
SEARCH_PROFILES: dict[str, dict[str, str]] = {
    "b2_06b_baseline": {"index_config": "A_B2", "query_template_name": "none"},          # 기본 (0.6B 기준선)
    "b2_4b_instruct": {"index_config": "A_B2_4B", "query_template_name": "instruct_v1"},  # 유력 후보 (최종 미확정)
    "b2_06b_v140": {"index_config": "A_B2_V140", "query_template_name": "none"},
    "b3_06b_v140": {"index_config": "B_B3_V140", "query_template_name": "none"},
}
DEFAULT_PROFILE = "b2_06b_baseline"
DATASET_DEFAULT_PROFILES = {"1.4.0-candidate": "b2_06b_v140"}
_UNSET = object()     # query_template을 "넘기지 않음"과 "None(지시문 없음)"을 구분하기 위한 표시
DEFAULT_K = 5
MAX_K = 200                                     # 진단용 전체 순위(107개)까지 허용하되 무제한은 막는다
MAX_QUERY_CHARS = 2000
MAX_POLICY_IDS = 50
MAX_FILTER_VALUES = 50
MAX_CONDITION_DEPTH = 10                        # ConditionGroup 하위 그룹 최대 깊이 (실제 데이터는 1단계)
# Qdrant 필터로 받을 수 있는 필드 = 실험 컬렉션에 payload 인덱스를 만든 필드
ALLOWED_FILTER_FIELDS: tuple[str, ...] = tuple(store.PAYLOAD_INDEX_FIELDS)
# 데이터 계약 §1의 정책 ID 형식
POLICY_ID_PATTERN = re.compile(r"^POL_[A-Z0-9_]+$")
B_CHUNK_ID_PATTERN = re.compile(r"^CHK_[A-Z0-9]+_(DOC_[A-Z0-9_]+)_C\d+$")

# 검색 결과 한 건에 담는 payload 필드 (원문 근거·위치·출처·버전)
HIT_PAYLOAD_FIELDS = (
    "policy_id", "document_id", "chunk_id", "point_id", "policy_name",
    "text", "char_start", "char_end", "page_start", "page_end", "section_title", "region",
    "source_url", "source_type", "content_hash", "verification_status",
    "chunking_version", "embedding_model", "embedding_input_rule", "index_run_id",
)

# 교차 검증 상태 값
PASS, MISSING, MISMATCH, NOT_TESTED = "PASS", "MISSING", "MISMATCH", "NOT_TESTED"

SCORE_NOTE = ("score는 질문 벡터와 청크 벡터의 코사인 유사도(Qdrant Cosine)다. 높을수록 의미가 가깝다는 뜻일 뿐, "
              "정책 자격을 충족한다는 뜻이 아니다.")
ELIGIBILITY_NOTE = ("자격 충족 여부는 판정하지 않았다. condition_tree는 원본 ConditionGroup의 logic(AND/OR/NOT/UNKNOWN)과 "
                    "계층을 그대로 보존한 것이며, UNKNOWN 그룹·review_status=extracted 근거는 공고문 확인이 필요하다.")

# 타임아웃 기본값 (초). 근거: 2026-11-05 데스크톱 실측 — Qdrant 검색 0.003~0.03초, Neo4j 정책 문맥 0.007~0.27초,
# Ollama 질의 임베딩은 모델이 메모리에 없으면 첫 로딩 최대 약 21초. 실측보다 넉넉하게 두되 무한 대기는 막는다.
QDRANT_TIMEOUT_SECONDS = 10                 # QdrantClient(timeout=) — REST 요청 1건 (qdrant-client 기본값은 None)
NEO4J_CONNECTION_TIMEOUT_SECONDS = 5.0      # 드라이버 connection_timeout (라이브러리 기본 30초)
NEO4J_ACQUISITION_TIMEOUT_SECONDS = 10.0    # 드라이버 connection_acquisition_timeout (라이브러리 기본 60초)
NEO4J_QUERY_TIMEOUT_SECONDS = 10.0          # 조회 트랜잭션 시간 제한 neo4j.Query(timeout=) (라이브러리 기본 없음)
QUERY_EMBED_TIMEOUT_SECONDS = em.DEFAULT_TIMEOUT_SECONDS   # Ollama 질의 임베딩 (기존 기본 60초 그대로)
MAX_RUNNER_NOTIFICATIONS = 1000             # 내부 읽기 실행기가 보관하는 Neo4j 알림 최대 개수 (오래된 것부터 버림)

# ---- 데이터 품질 경고 (결과의 순위·내용은 바꾸지 않고 표시만 한다) ----
# 2026-11-05 조사(인수인계서 §12)에서 확인한 "의심 패턴". 자격 조건이 틀렸다고 확정하는 규칙이 아니다.
HOMELESS_CONDITION_ID = "COND_HH_무주택세대"
INCOME_BAND_CONDITION_IDS = frozenset({"COND_INC_중위소득_0_50_", "COND_INC_중위소득_51_75_",
                                       "COND_INC_중위소득_76_100_", "COND_INC_중위소득_101_200_",
                                       "COND_INC_중위소득_200_초과"})
# 그래프 Policy 노드의 긴 텍스트 속성 (수집 단계 추출값 — 원문 근거 청크가 아님)
GRAPH_EXTRACTED_TEXT_FIELDS = ("eligibility", "benefit", "application_period", "application_method")
QUALITY_UNKNOWN_CONDITION = "UNKNOWN_CONDITION_GROUP"
QUALITY_EXTRACTED_NOT_VERIFIED = "EXTRACTED_NOT_VERIFIED"
QUALITY_HOMELESS_PROMOTED = "SUSPECTED_HOMELESS_REQUIRED_WITH_HOUSEHOLD_OR"
QUALITY_INCOME_ALL_BANDS = "SUSPECTED_INCOME_ALL_BANDS_OR"
QUALITY_GRAPH_MISSING = "GRAPH_CONTEXT_MISSING"
QUALITY_STRUCTURE_ISSUE = "CONDITION_STRUCTURE_ISSUE"
# 2026-11-06 Neo4j 의미 점검 결과로 추가한 그래프 계산 경고 (모두 의심 후보, 확정 아님)
QUALITY_EMPTY_NOT_GROUP = "EMPTY_NOT_GROUP"
QUALITY_EMPTY_AND_GROUP = "EMPTY_AND_GROUP"
QUALITY_NEEDS_REVIEW = "NEEDS_REVIEW"
QUALITY_REGION_GRANULARITY = "SUSPECTED_REGION_GRANULARITY_LOST"
# 선택 속성 Policy.region_detail이 있으면 시군구 조건 확인 필요 (지역 범위 추론 없음).
QUALITY_REGION_DETAIL = "REGION_LIMITED_TO_MUNICIPALITY"
QUALITY_SEARCH_REGION = "SEARCH_REGION_REQUIRES_REVIEW"
QUALITY_REVIEW_STATUS_OVERCLAIM = "SUSPECTED_REVIEW_STATUS_OVERCLAIM"
# 경고 코드별 메타: severity / evidence_level(근거 종류) / verification(검증 상태) / affects(판정 영향 항목)
#   / blocks_auto_eligibility(자동 판정 보류 필요). 공식 원문 검증은 모두 하지 않았다.
QUALITY_CODE_META: dict[str, dict[str, Any]] = {
    QUALITY_SEARCH_REGION: {"severity": "medium", "evidence_level": "stored_region_string",
                            "verification": "not_officially_checked", "affects": ["region"],
                            "blocks_auto_eligibility": True},
    QUALITY_EMPTY_AND_GROUP: {"severity": "high", "evidence_level": "graph_structure", "verification": "not_modeled",
                              "affects": ["all"], "blocks_auto_eligibility": True},
    QUALITY_NEEDS_REVIEW: {"severity": "high", "evidence_level": "graph_metadata", "verification": "needs_review",
                           "affects": ["all"], "blocks_auto_eligibility": True},
    "GRAPH_CONTEXT_MISSING": {"severity": "high", "evidence_level": "graph_structure", "verification": "not_modeled",
                              "affects": ["all"], "blocks_auto_eligibility": True},
    "UNKNOWN_CONDITION_GROUP": {"severity": "high", "evidence_level": "graph_structure", "verification": "not_modeled",
                                "affects": ["all"], "blocks_auto_eligibility": True},
    "CONDITION_STRUCTURE_ISSUE": {"severity": "high", "evidence_level": "graph_structure", "verification": "suspected",
                                  "affects": ["all"], "blocks_auto_eligibility": True},
    "EXTRACTED_NOT_VERIFIED": {"severity": "low", "evidence_level": "graph_metadata", "verification": "not_officially_checked",
                               "affects": [], "blocks_auto_eligibility": False},
    "SUSPECTED_HOMELESS_REQUIRED_WITH_HOUSEHOLD_OR": {"severity": "high", "evidence_level": "graph_pattern",
                                                      "verification": "suspected", "affects": ["housing"],
                                                      "blocks_auto_eligibility": True},
    "SUSPECTED_INCOME_ALL_BANDS_OR": {"severity": "high", "evidence_level": "graph_pattern", "verification": "suspected",
                                      "affects": ["income"], "blocks_auto_eligibility": True},
    QUALITY_EMPTY_NOT_GROUP: {"severity": "medium", "evidence_level": "graph_structure", "verification": "not_modeled",
                              "affects": ["exclusion"], "blocks_auto_eligibility": True},
    QUALITY_REGION_GRANULARITY: {"severity": "high", "evidence_level": "graph_property_pattern",
                                 "verification": "suspected", "affects": ["region"], "blocks_auto_eligibility": True},
    QUALITY_REGION_DETAIL: {"severity": "medium", "evidence_level": "graph_metadata", "verification": "source_text",
                            "affects": ["region"], "blocks_auto_eligibility": True},
    QUALITY_REVIEW_STATUS_OVERCLAIM: {"severity": "low", "evidence_level": "graph_metadata", "verification": "suspected",
                                      "affects": [], "blocks_auto_eligibility": False},
    # 정적 목록(data_quality_flags) 코드: 심각도·근거 수준은 목록 값을 그대로 쓴다
    "EMPLOYMENT_NOT_MODELED": {"affects": ["employment"], "blocks_auto_eligibility": True},
    "AGE_SOURCE_GRAPH_MISMATCH": {"affects": ["age"], "blocks_auto_eligibility": True},
    "AGE_LOWER_BOUND_WITHOUT_SOURCE": {"affects": ["age"], "blocks_auto_eligibility": True},
    "EXCLUSION_NOT_MODELED": {"affects": ["exclusion"], "blocks_auto_eligibility": True},
    "MARRIAGE_CHILD_NOT_MODELED": {"affects": ["marriage", "children"], "blocks_auto_eligibility": True},
    "SOURCE_AGE_ERROR_MARK": {"affects": ["age"], "blocks_auto_eligibility": True},
}
QUALITY_STATIC_MESSAGES = {
    "EMPLOYMENT_NOT_MODELED": "인계 속성의 취업 상태 요건이 그래프 조건에 없음 → 취업 상태는 추가 질문으로 확인 (확정 아님)",
    "AGE_SOURCE_GRAPH_MISMATCH": "원문에 명시된 연령 범위와 그래프 연령 조건이 다름 → 원문 연령 우선, 판정 보류 (공식 원문 미검증)",
    "AGE_LOWER_BOUND_WITHOUT_SOURCE": "원문에 연령 언급이 없는데 그래프는 연령 하한을 둠 → 연령으로 탈락시키지 말 것 (확정 아님)",
    "EXCLUSION_NOT_MODELED": "원문의 제외·참여 제한 문구가 그래프 조건에 없음(키워드 탐지) → 제외 대상 여부는 추가 질문 (확정 아님)",
    "MARRIAGE_CHILD_NOT_MODELED": "혼인·자녀 관련 문구가 있으나 해당 조건 타입이 그래프에 없음(키워드 탐지) → 추가 질문",
    "SOURCE_AGE_ERROR_MARK": "수집 원문에 연령 표기 오류 표시(0~0세)가 있음 → 연령 판정 보류, 공고 확인",
}
DATA_QUALITY_NOTES = {
    "unknown": "logic=UNKNOWN 또는 조건 0개는 '자격 제한 없음'이 아니라 '구조화 조건 없음'이다. 공고 원문 확인이 필요하다.",
    "extracted": "review_status=extracted는 자동 추출값이며 공식 검증 완료가 아니다.",
    "gov24_rule": ("정부24 지원조건(JA 코드)에서 만든 조건 트리에 생성 규칙 오류 의심 사례가 있다 "
                   "(무주택 필수 승격, 소득 전 구간 OR, 가구 '해당사항없음' 누락, 취업 조건 미반영). 확정된 오류는 아니다."),
    "graph_text": ("graph.policy의 eligibility·benefit 등은 수집 단계 추출 텍스트다. 답변 근거로는 evidence[].text(원문 청크)와 "
                   "source_url을 쓴다."),
    "hold": ("data_quality.hold_auto_eligibility=True는 '부적격'이 아니라 '자동 판정 보류'다. affected_attributes의 항목은 "
             "원문 근거 제시·추가 질문으로 확인한다. 경고는 모두 자동 점검 의심 후보이며 공식 원문과 대조하지 않았다."),
}


class HybridSearchInputError(ValueError):
    """잘못된 입력 (빈 질문, 허용되지 않은 필터 필드, 형식이 틀린 policy_id, 범위 밖 k 등). DB에 접속하지 않았다."""


class SearchProfileError(HybridSearchInputError):
    """검색 프로필·컬렉션·임베딩 모델·차원·지시문이 서로 맞지 않음. 검색(Qdrant 호출) 전에 낸다."""


class HybridSearchBackendError(RuntimeError):
    """
    백엔드 장애. backend 속성: "ollama" / "qdrant" / "neo4j" (기존과 같음).
    kind 속성(추가): "unavailable"(접속 불가) / "timeout" / "auth"(인증 실패) / "config"(설정 누락·형식 오류) / "server"(서버 오류 응답)
    메시지에 비밀번호·API 키·URL의 사용자 정보를 넣지 않는다 (_safe_message).
    코드 버그(쿼리 문법 오류, KeyError 등)는 이 오류로 감싸지 않고 원래 예외 그대로 올라간다.
    """

    def __init__(self, backend: str, message: str, kind: str = "unavailable"):
        super().__init__(f"[{backend}:{kind}] {message}")
        self.backend = backend
        self.kind = kind


_URL_USERINFO = re.compile(r"(?P<scheme>[a-z][a-z0-9+.-]*://)[^/@\s]+@", re.IGNORECASE)
_SECRET_ENV_NAMES = ("NEO4J_PASSWORD", "QDRANT_API_KEY")


def _safe_message(error: BaseException, limit: int = 300) -> str:
    """예외 메시지에서 비밀값(.env의 비밀번호·API 키)과 URL 사용자 정보(user:pass@)를 지운다."""
    text = f"{type(error).__name__}: {error}"
    for name in _SECRET_ENV_NAMES:
        value = os.environ.get(name)
        if value and len(value) >= 4:
            text = text.replace(value, "***")
    text = _URL_USERINFO.sub(r"\g<scheme>***@", text)
    return text if len(text) <= limit else text[:limit] + "…"


def _qdrant_error(error: BaseException) -> HybridSearchBackendError | None:
    """qdrant-client·httpx 예외면 HybridSearchBackendError로 바꾼다. 그 밖의 예외(코드 버그)는 None → 원래대로 올린다."""
    try:
        import httpx
        from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse
    except ImportError:   # pragma: no cover - 패키지가 없으면 표준 예외만 분류
        httpx = ResponseHandlingException = UnexpectedResponse = None
    if ResponseHandlingException is not None and isinstance(error, ResponseHandlingException):
        source = getattr(error, "source", None)
        if httpx is not None and isinstance(source, httpx.TimeoutException):
            return HybridSearchBackendError("qdrant", f"요청 시간 초과: {_safe_message(source)}", "timeout")
        if httpx is not None and isinstance(source, httpx.HTTPError):
            return HybridSearchBackendError("qdrant", f"접속 실패: {_safe_message(source)}", "unavailable")
        return None   # 응답 형식 검증 실패 등은 서버 장애가 아닐 수 있다 → 그대로 올린다
    if UnexpectedResponse is not None and isinstance(error, UnexpectedResponse):
        status = getattr(error, "status_code", None)
        kind = "auth" if status in (401, 403) else "not_found" if status == 404 else "server"
        return HybridSearchBackendError("qdrant", f"서버 오류 응답 (HTTP {status})", kind)
    if httpx is not None and isinstance(error, httpx.TimeoutException):
        return HybridSearchBackendError("qdrant", f"요청 시간 초과: {_safe_message(error)}", "timeout")
    if isinstance(error, TimeoutError):
        return HybridSearchBackendError("qdrant", f"요청 시간 초과: {_safe_message(error)}", "timeout")
    if isinstance(error, (ConnectionError, OSError)) or (httpx is not None and isinstance(error, httpx.HTTPError)):
        return HybridSearchBackendError("qdrant", f"접속 실패: {_safe_message(error)}", "unavailable")
    return None


def _neo4j_error(error: BaseException) -> HybridSearchBackendError | None:
    """neo4j 드라이버 예외 중 서버·연결 장애면 HybridSearchBackendError로 바꾼다. 쿼리 문법 오류 등 코드 버그는 None."""
    try:
        from neo4j import exceptions as ne
    except ImportError:   # pragma: no cover
        ne = None
    if ne is not None:
        if isinstance(error, ne.ConfigurationError):
            return HybridSearchBackendError("neo4j", f"드라이버 설정 오류: {_safe_message(error)}", "config")
        if isinstance(error, ne.AuthError):
            return HybridSearchBackendError("neo4j", "인증 실패 (NEO4J_USER·NEO4J_PASSWORD 확인)", "auth")
        if isinstance(error, ne.ClientError) and "TransactionTimedOut" in str(getattr(error, "code", "") or ""):
            return HybridSearchBackendError("neo4j", f"조회 시간 초과: {_safe_message(error)}", "timeout")
        if isinstance(error, (ne.ServiceUnavailable, ne.SessionExpired)):
            return HybridSearchBackendError("neo4j", f"접속 불가: {_safe_message(error)}", "unavailable")
        if isinstance(error, (ne.TransientError, ne.DatabaseError)):
            return HybridSearchBackendError("neo4j", f"서버 오류: {_safe_message(error)}", "server")
        if isinstance(error, ne.DriverError):
            return HybridSearchBackendError("neo4j", f"드라이버 오류: {_safe_message(error)}", "unavailable")
    if isinstance(error, TimeoutError):
        return HybridSearchBackendError("neo4j", f"시간 초과: {_safe_message(error)}", "timeout")
    if isinstance(error, (ConnectionError, OSError)):
        return HybridSearchBackendError("neo4j", f"접속 실패: {_safe_message(error)}", "unavailable")
    return None   # ClientError(문법·파라미터 오류)·KeyError·TypeError 등 → 코드 문제로 보고 원래 예외를 올린다


# ---------------------------------------------------------------------------
# 2. 입력 검증 (DB 접속 전)
# ---------------------------------------------------------------------------
def validate_query(query: Any) -> str:
    if not isinstance(query, str) or not query.strip():
        raise HybridSearchInputError("query는 비어 있지 않은 문자열이어야 합니다.")
    if len(query) > MAX_QUERY_CHARS:
        raise HybridSearchInputError(f"query가 너무 깁니다: {len(query)}자 (최대 {MAX_QUERY_CHARS}자)")
    return query.strip()


def validate_k(k: Any) -> int:
    if isinstance(k, bool) or not isinstance(k, int) or not 1 <= k <= MAX_K:
        raise HybridSearchInputError(f"k는 1 이상 {MAX_K} 이하의 정수여야 합니다: {k!r}")
    return k


def validate_filters(filters: Any) -> dict[str, Any] | None:
    """
    허용 필드의 값만 받는다. 값: 비어 있지 않은 문자열 1개, 또는 그런 문자열의 리스트(MatchAny, 하나라도 일치).
    여러 필드는 모두 만족(AND). 예) {"document_id": "DOC_LH_001"}, {"policy_id": ["POL_001", "POL_003"]}
    """
    if filters is None or filters == {}:
        return None
    if not isinstance(filters, dict):
        raise HybridSearchInputError("filters는 dict 또는 None이어야 합니다.")
    normalized: dict[str, Any] = {}
    for key, value in filters.items():
        if key not in ALLOWED_FILTER_FIELDS:
            raise HybridSearchInputError(f"허용되지 않은 필터 필드: {key!r} (허용: {', '.join(ALLOWED_FILTER_FIELDS)})")
        values = list(value) if isinstance(value, (list, tuple)) else [value]
        if not values or len(values) > MAX_FILTER_VALUES:
            raise HybridSearchInputError(f"필터 {key}의 값 개수는 1~{MAX_FILTER_VALUES}개여야 합니다.")
        if not all(isinstance(item, str) and item.strip() for item in values):
            raise HybridSearchInputError(f"필터 {key}의 값은 비어 있지 않은 문자열이어야 합니다.")
        normalized[key] = values if isinstance(value, (list, tuple)) else values[0]
    return normalized


def validate_policy_ids(policy_ids: Any) -> list[str]:
    """정책 ID 목록 검사 + 순서를 지킨 중복 제거. 형식: 데이터 계약 ^POL_[A-Z0-9_]+$"""
    if not isinstance(policy_ids, (list, tuple)):
        raise HybridSearchInputError("policy_ids는 문자열 리스트여야 합니다.")
    unique: list[str] = []
    for policy_id in policy_ids:
        if not isinstance(policy_id, str) or not POLICY_ID_PATTERN.match(policy_id):
            raise HybridSearchInputError(f"policy_id 형식이 아님: {policy_id!r}")
        if policy_id not in unique:
            unique.append(policy_id)
    if len(unique) > MAX_POLICY_IDS:
        raise HybridSearchInputError(f"policy_ids는 최대 {MAX_POLICY_IDS}개입니다: {len(unique)}개")
    return unique


# ---------------------------------------------------------------------------
# 2-1. 연결 만들기·닫기 (내부 생성 리소스만 닫는다)
# ---------------------------------------------------------------------------
def _close_quietly(resource) -> BaseException | None:
    """close()를 부르고, 닫는 중 오류는 삼키지 않고 돌려준다(원래 예외를 가리지 않기 위해 여기서 올리지 않는다)."""
    if resource is None:
        return None
    try:
        resource.close()
    except Exception as error:   # 닫기 실패가 검색 결과·원래 오류를 덮지 않게 한다
        return error
    return None


def make_qdrant_client(timeout: int = QDRANT_TIMEOUT_SECONDS):
    """
    .env(QDRANT_URL·QDRANT_API_KEY)로 QdrantClient를 만든다. 요청 타임아웃을 지정한다. (서버에 바로 접속하지 않는다)
    호출한 쪽이 close()해야 한다. 설정 누락은 HybridSearchBackendError(backend="qdrant", kind="config").
    """
    try:
        from qdrant_client import QdrantClient
    except ImportError as error:
        raise HybridSearchBackendError("qdrant", "qdrant-client가 설치되어 있지 않음", "config") from error
    try:
        url, api_key = store.resolve_connection_settings()
    except store.ConnectionSettingsError as error:
        raise HybridSearchBackendError("qdrant", f"접속 설정 문제: {_safe_message(error)}", "config") from error
    try:
        return QdrantClient(url=url, api_key=api_key, timeout=timeout)
    except Exception as error:
        converted = _qdrant_error(error)
        if converted is not None:
            raise converted from error
        raise HybridSearchBackendError("qdrant", f"클라이언트 생성 실패: {_safe_message(error)}", "config") from error


class ReadOnlyTimedRunner(lg.Neo4jDriverRunner):
    """
    Hybrid 검색용 Neo4j 실행기: 읽기 전용 세션 + 조회 시간 제한 + 알림 보관 상한.
    - read_rows: neo4j.Query(timeout=query_timeout)로 실행 → 서버가 시간 초과 트랜잭션을 끝낸다.
    - run_schema·write_batch: 호출하면 PermissionError (이 모듈은 쓰지 않는다)
    - notifications: 최대 max_notifications개만 보관하고, 버린 개수는 dropped_notifications에 센다.
    """

    def __init__(self, driver, database: str | None = None, *, query_timeout: float | None = NEO4J_QUERY_TIMEOUT_SECONDS,
                 max_notifications: int = MAX_RUNNER_NOTIFICATIONS):
        super().__init__(driver, database)
        self.query_timeout = query_timeout
        self.max_notifications = max_notifications
        self.dropped_notifications = 0

    def run_schema(self, statement: str) -> None:
        raise PermissionError("Hybrid 검색 실행기는 읽기 전용입니다 (스키마 쓰기 금지)")

    def write_batch(self, query: str, rows: list[dict[str, Any]], expected: int) -> int:
        raise PermissionError("Hybrid 검색 실행기는 읽기 전용입니다 (데이터 쓰기 금지)")

    def read_rows(self, query: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        from neo4j import Query

        with self._session(lg.READ_ACCESS_MODE) as session:
            result = session.run(Query(query, timeout=self.query_timeout), parameters or {})
            rows = [record.data() for record in result]
            summary = result.consume()
        self.notifications.extend(lg._notification_records(summary, query))
        overflow = len(self.notifications) - self.max_notifications
        if overflow > 0:
            del self.notifications[:overflow]
            self.dropped_notifications += overflow
        return rows


def _make_neo4j_driver(connection_timeout: float, acquisition_timeout: float):
    """.env의 NEO4J_URI·NEO4J_USER·NEO4J_PASSWORD로 드라이버를 만들고 연결을 확인한다. 실패하면 HybridSearchBackendError."""
    try:
        from neo4j import GraphDatabase
    except ImportError as error:
        raise HybridSearchBackendError("neo4j", "neo4j 드라이버가 설치되어 있지 않음", "config") from error
    em.load_env_file()
    missing = [name for name in ("NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD") if not os.environ.get(name)]
    if missing:
        raise HybridSearchBackendError("neo4j", f"접속 설정 누락: {', '.join(missing)} (.env 확인)", "config")
    try:
        driver = GraphDatabase.driver(os.environ["NEO4J_URI"], auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]),
                                      connection_timeout=connection_timeout,
                                      connection_acquisition_timeout=acquisition_timeout)
    except Exception as error:   # URI 형식 오류(ValueError)·ConfigurationError 등 → 설정 문제
        converted = _neo4j_error(error)
        if converted is not None and converted.kind != "unavailable":
            raise converted from error
        raise HybridSearchBackendError("neo4j", f"드라이버 생성 실패 (NEO4J_URI 형식 확인): {_safe_message(error)}",
                                       "config") from error
    try:
        driver.verify_connectivity()
    except Exception as error:
        _close_quietly(driver)
        converted = _neo4j_error(error)
        if converted is None:
            raise
        raise converted from error
    return driver


# ---------------------------------------------------------------------------
# 3. Qdrant 근거 검색
# ---------------------------------------------------------------------------
def _hit_record(hit: dict[str, Any]) -> dict[str, Any]:
    payload = hit.get("payload") or {}
    record = {"rank": hit["rank"], "score": hit["score"]}
    record.update({field: payload.get(field) for field in HIT_PAYLOAD_FIELDS})
    record["point_id"] = record["point_id"] or hit.get("point_id")
    return record


def resolve_search_profile(name: str) -> dict[str, Any]:
    """프로필 이름 → {name, index_config, collection, embedding_model, embedding_dim, query_template_name, query_template, …}"""
    if name not in SEARCH_PROFILES:
        raise SearchProfileError(f"알 수 없는 검색 프로필: {name!r} (허용: {', '.join(SEARCH_PROFILES)})")
    spec = SEARCH_PROFILES[name]
    config = fi.get_config(spec["index_config"])
    model, dim = fi.config_model(config)
    return {"name": name, "index_config": spec["index_config"], "collection": config["collection"],
            "embedding_model": model, "embedding_dim": dim, "distance": fi.DISTANCE,
            "embedding_input_rule": fi.EMBEDDING_INPUT_RULE,
            "query_template_name": spec["query_template_name"],
            "query_template": rr.QUERY_TEMPLATES[spec["query_template_name"]]}


def default_profile() -> str:
    """현재 인계 데이터셋의 기본 검색 프로필. v1.3.2(data/handoff)는 DEFAULT_PROFILE 그대로."""
    return DATASET_DEFAULT_PROFILES.get(hd.dataset_version(), DEFAULT_PROFILE)


def resolve_search_settings(*, profile: str | None = None, collection: str | None = None,
                            embedder: em.Embedder | None = None, query_template: Any = _UNSET) -> dict[str, Any]:
    """
    검색 설정을 정한다 (DB 접속 없음). 두 방식 중 하나다.

    1) 프로필 방식 (collection을 주지 않음, 기본 DEFAULT_PROFILE)
       - 컬렉션·모델·차원·지시문을 프로필이 정한다.
       - query_template을 넘기면 프로필 지시문과 같아야 한다 (다르면 SearchProfileError).
       - embedder를 넘기면 그 함수에 embedding_model·embedding_dim 표시가 있고 프로필과 같아야 한다
         (embedding.make_ollama_embedder가 붙여 준다). 표시가 없거나 다르면 SearchProfileError → 검증 우회 불가.
       - 실제 질의 벡터 차원과 컬렉션 벡터 크기도 검색 전에 다시 확인한다 (_search_evidence).
    2) 사용자 지정 방식 (collection을 직접 줌, 기존 호출과 같음)
       - 컬렉션·embedder·지시문은 호출한 쪽이 정한다 (embedder 없으면 0.6B, 지시문 기본 없음).
       - 차원은 검색 전에 컬렉션 벡터 크기와 비교한다. profile과 함께 줄 수 없다.
    """
    if collection is not None:
        if profile is not None:
            raise SearchProfileError("profile과 collection을 함께 줄 수 없습니다 (하나만 선택).")
        store.assert_experiment_collection(collection)
        # 이름을 아는 컬렉션은 직접 지정 모드에서도 기존 데이터셋 guard를 거친다.
        for name, config in fi.FULL_INDEX_CONFIGS.items():
            if config["collection"] == collection:
                fi.get_config(name)
        if collection == LEGACY_EXPERIMENT_COLLECTION and hd.dataset_version() != fi.DEFAULT_DATASET_VERSION:
            raise SearchProfileError("기존 107 Point 컬렉션은 v1.3.2 전용입니다.")
        return {"mode": "custom", "name": None, "index_config": None, "collection": collection,
                "embedding_model": em.DEFAULT_EMBEDDING_MODEL if embedder is None else getattr(embedder, "embedding_model", None),
                "embedding_dim": em.DEFAULT_EMBEDDING_DIM if embedder is None else getattr(embedder, "embedding_dim", None),
                "query_template_name": None, "query_template": None if query_template is _UNSET else query_template,
                "embedder": embedder}
    resolved = resolve_search_profile(profile or default_profile())
    if query_template is not _UNSET and query_template != resolved["query_template"]:
        raise SearchProfileError(f"프로필 {resolved['name']}의 질의 지시문({resolved['query_template_name']})과 다른 "
                                 "query_template은 쓸 수 없습니다.")
    if embedder is not None:
        declared = (getattr(embedder, "embedding_model", None), getattr(embedder, "embedding_dim", None))
        if declared != (resolved["embedding_model"], resolved["embedding_dim"]):
            raise SearchProfileError(f"embedder 표시 {declared} ≠ 프로필 {resolved['name']} "
                                     f"({resolved['embedding_model']}, {resolved['embedding_dim']})")
    return {"mode": "profile", **resolved, "embedder": embedder}


def _collection_vector_size(client, collection: str) -> int:
    """컬렉션 벡터 크기(읽기 전용). Qdrant 장애는 HybridSearchBackendError로 바꾼다."""
    try:
        return int(client.get_collection(collection).config.params.vectors.size)
    except Exception as error:
        if isinstance(error, ValueError) and str(error) == f"Collection {collection} not found":
            raise HybridSearchBackendError("qdrant", f"컬렉션이 없습니다: {collection} (색인 적재 필요)", "not_found") from error
        converted = _qdrant_error(error)
        if converted is None:
            raise
        if converted.kind == "not_found":
            raise HybridSearchBackendError("qdrant", f"컬렉션이 없습니다: {collection} (색인 적재 필요)", "not_found") from error
        raise converted from error


def search_policy_evidence(query: str, filters: dict[str, Any] | None = None, k: int = DEFAULT_K, *,
                           client=None, embedder: em.Embedder | None = None,
                           collection: str | None = None, query_template: Any = _UNSET,
                           profile: str | None = None) -> dict[str, Any]:
    """
    질문을 임베딩해 Qdrant에서 Top-K 근거 청크를 찾는다. (읽기 전용)
    입력
      query          : 질문 문자열
      filters        : None 또는 {허용 필드: 값 | [값, ...]} (validate_filters 참고)
      k              : 돌려받을 청크 수 (1~MAX_K)
      client         : QdrantClient (None이면 .env로 store.get_client())
      profile        : 검색 프로필 이름 (기본 DEFAULT_PROFILE = 0.6B 전체 색인). resolve_search_settings 참고
      collection     : 직접 지정할 컬렉션 (주면 사용자 지정 방식, 기존 호출과 같음)
      embedder       : 문자열 목록 → 벡터 목록 함수 (None이면 프로필 모델 또는 Ollama qwen3-embedding:0.6b, batch 1)
      query_template : 사용자 지정 방식에서만 자유롭게 쓴다 (프로필 방식은 프로필 지시문)
    반환
      {"query", "query_template", "filters", "k", "collection", "query_embedding_model",
       "latency_seconds": {"query_embedding", "qdrant_search"}, "warnings": [...],
       "hits": [{"rank", "score", "policy_id", "document_id", "chunk_id", "text", "char_start", "char_end",
                 "page_start", "page_end", "section_title", "source_url", "source_type", ...}, ...],  (rank 오름차순)
       "search_profile": {mode, name, collection, embedding_model, embedding_dim, collection_vector_size,
                          query_template_name, query_template, ...}}   ← 2026-11-06 추가 필드
    오류: HybridSearchInputError(입력·SearchProfileError), HybridSearchBackendError(backend="ollama"|"qdrant")
    """
    query, filters, k = validate_query(query), validate_filters(filters), validate_k(k)
    settings = resolve_search_settings(profile=profile, collection=collection, embedder=embedder,
                                       query_template=query_template)
    if client is not None:      # 외부에서 받은 client → 닫지 않는다
        return _search_evidence(client, query, filters, k, settings)
    owned = make_qdrant_client()   # 이 호출에서 만든 client → 정상·예외 모두 닫는다
    try:
        return _search_evidence(owned, query, filters, k, settings)
    finally:
        _close_quietly(owned)


def _search_evidence(client, query: str, filters: dict[str, Any] | None, k: int,
                     settings: dict[str, Any], *, vector_size: int | None = None,
                     region_catalog=None, refresh_vector_size: Callable[[], int] | None = None) -> dict[str, Any]:
    """검증을 마친 입력·설정으로 실제 임베딩·검색을 한다. client는 호출한 쪽이 관리한다."""
    collection, query_template = settings["collection"], settings["query_template"]
    # (1) 컬렉션 벡터 크기와 설정 차원을 검색 전에 비교한다 (프로필, 또는 차원을 표시한 embedder)
    #     사용자 지정 방식에서 embedder를 주지 않으면 기본값으로 단정하지 않고 (2)의 실제 벡터 차원 검사로 막는다
    if vector_size is None:
        vector_size = _collection_vector_size(client, collection)
    region_plan = None
    if region_catalog is not None or hd.dataset_version() == "1.4.0-candidate":
        region_plan = (rs.plan_region_search(filters, hd.load_payload_chunks()) if region_catalog is None
                       else rs.plan_region_search(filters, region_catalog=region_catalog))
    applied_filters = region_plan["applied_filters"] if region_plan is not None else filters
    declared_dim = settings["embedding_dim"] if (settings["mode"] == "profile" or settings["embedder"] is not None) else None
    if declared_dim is not None and declared_dim != vector_size and refresh_vector_size is not None:
        vector_size = refresh_vector_size()
        refresh_vector_size = None
    if declared_dim is not None and declared_dim != vector_size:
        raise SearchProfileError(f"{collection} 벡터 크기 {vector_size} ≠ 임베딩 차원 {settings['embedding_dim']} "
                                 f"({settings['embedding_model']})")
    embedder = settings["embedder"]
    if embedder is None:
        if settings["mode"] == "profile":
            embedder = em.make_ollama_embedder(model=settings["embedding_model"], expected_dim=settings["embedding_dim"],
                                               batch_size=1, timeout=QUERY_EMBED_TIMEOUT_SECONDS)
        else:
            embedder = em.make_ollama_embedder(batch_size=1, timeout=QUERY_EMBED_TIMEOUT_SECONDS)
    # 질의 모델 이름: 프로필 모델, 기본 0.6B, 또는 embedder 표시. 표시 없는 가짜 embedder는 None (단정하지 않음)
    query_model = settings["embedding_model"]

    started = time.perf_counter()
    try:
        query_vector = embedder([em.build_query_text(query, query_template)])[0]
    except em.EmbeddingError as error:
        kind = "timeout" if "시간 초과" in str(error) else "unavailable"
        raise HybridSearchBackendError("ollama", f"질의 임베딩 실패: {_safe_message(error)}", kind) from error
    except requests.RequestException as error:
        kind = "timeout" if isinstance(error, requests.Timeout) else "unavailable"
        raise HybridSearchBackendError("ollama", f"질의 임베딩 실패: {_safe_message(error)}", kind) from error
    embed_seconds = time.perf_counter() - started
    # (2) 실제 질의 벡터 차원도 검색 전에 확인한다 (표시를 속인 embedder도 여기서 막힌다)
    if len(query_vector) != vector_size and refresh_vector_size is not None:
        vector_size = refresh_vector_size()
    if len(query_vector) != vector_size:
        raise SearchProfileError(f"질의 벡터 차원 {len(query_vector)} ≠ {collection} 벡터 크기 {vector_size}")

    started = time.perf_counter()
    try:
        hits = store.search_chunks(client, collection, query_vector, k=k, filters=applied_filters)
    except Exception as error:
        converted = _qdrant_error(error)
        if converted is None:
            raise                       # 코드 버그·컬렉션 보호 오류는 원래 예외 그대로
        raise converted from error
    search_seconds = time.perf_counter() - started

    records = [_hit_record(hit) for hit in hits]
    warnings = []
    if region_plan is not None:
        for record in records:
            record["region_match"] = rs.describe_hit_region(record["region"], region_plan)
        if any(record["region_match"]["review_required"] for record in records):
            warnings.append(rs.REVIEW_NOTE)
    index_models = sorted({record["embedding_model"] for record in records if record["embedding_model"]})
    if query_model and index_models and index_models != [query_model]:
        warnings.append(f"질의 임베딩 모델({query_model})과 색인 모델({index_models})이 다름 → 점수 비교 불가")
    profile_meta = {key: settings.get(key) for key in ("mode", "name", "index_config", "collection", "embedding_model",
                                                        "embedding_dim", "query_template_name", "query_template",
                                                        "distance", "embedding_input_rule")}
    profile_meta["collection_vector_size"] = vector_size
    return {"query": query, "query_template": query_template, "filters": filters, "k": k, "collection": collection,
            **({"region_search": region_plan} if region_plan is not None else {}),
            "query_embedding_model": query_model,
            "latency_seconds": {"query_embedding": round(embed_seconds, 4), "qdrant_search": round(search_seconds, 4)},
            "warnings": warnings, "hits": records, "search_profile": profile_meta}


# ---------------------------------------------------------------------------
# 4. Neo4j 정책 문맥 (파라미터화된 읽기 전용 Cypher)
# ---------------------------------------------------------------------------
# (결과 키, 관계 타입) — 양끝 레이블은 load_graph.RELATIONSHIP_SPECS에서 가져온다 (허용 목록과 항상 같게)
POLICY_NEIGHBOR_SPECS: tuple[tuple[str, str], ...] = (
    ("organizations", "ADMINISTERED_BY"),
    ("regions", "AVAILABLE_IN"),
    ("categories", "HAS_CATEGORY"),
    ("target_groups", "TARGETS"),
    ("life_stages", "APPLIES_TO_LIFESTAGE"),
    ("documents", "SUPPORTED_BY"),
)


def _neighbor_comprehension(key: str, relation: str) -> str:
    source_label, target_label = lg.RELATIONSHIP_SPECS[lg._checked_relation(relation)]
    if source_label != "Policy":
        raise lg.GraphValidationError(f"정책에서 출발하는 관계가 아님: {relation}")
    return (f"[(p)-[r:{relation}]->(n:{target_label}) | "
            f"{{node: properties(n), relationship: properties(r)}}] AS {key}")


POLICY_CONTEXT_QUERY = (
    "UNWIND $policy_ids AS policy_id\n"
    "MATCH (p:Policy {policy_id: policy_id})\n"
    "RETURN policy_id, properties(p) AS policy,\n       "
    + ",\n       ".join(_neighbor_comprehension(key, relation) for key, relation in POLICY_NEIGHBOR_SPECS)
    + ",\n       [(p)-[r:MODIFIES_CONDITION_OF]->(n:Policy) | "
      "{policy_id: n.policy_id, policy_name: n.policy_name, relationship: properties(r)}] AS modifies_condition_of"
    + ",\n       [(n:Policy)-[r:MODIFIES_CONDITION_OF]->(p) | "
      "{policy_id: n.policy_id, policy_name: n.policy_name, relationship: properties(r)}] AS condition_modified_by"
    + ",\n       [(p)-[:REQUIRES_CONDITION]->(c:EligibilityCondition) | c.condition_id] AS requires_condition_ids"
    + ",\n       [(p)-[r:HAS_ROOT_CONDITION_GROUP]->(g:ConditionGroup) | "
      "{group_id: g.group_id, relationship: properties(r)}] AS root_groups"
)

# 루트 그룹에서 CONTAINS_GROUP을 따라 내려가며 그룹마다 하위 그룹·조건을 한 줄씩 읽는다. 트리 조립은 Python에서 한다.
CONDITION_TREE_QUERY = (
    "UNWIND $policy_ids AS policy_id\n"
    "MATCH (p:Policy {policy_id: policy_id})-[:HAS_ROOT_CONDITION_GROUP]->(root:ConditionGroup)\n"
    f"MATCH (root)-[:CONTAINS_GROUP*0..{int(MAX_CONDITION_DEPTH)}]->(g:ConditionGroup)\n"
    "WITH DISTINCT policy_id, root, g\n"
    "RETURN policy_id, root.group_id AS root_group_id, g.group_id AS group_id, properties(g) AS group,\n"
    "       [(g)-[r:CONTAINS_GROUP]->(child:ConditionGroup) | "
    "{group_id: child.group_id, relationship: properties(r)}] AS child_groups,\n"
    "       [(g)-[r:CONTAINS_CONDITION]->(c:EligibilityCondition) | "
    "{condition: properties(c), relationship: properties(r)}] AS conditions"
)

_NEIGHBOR_ID_FIELDS = {key: lg.LABEL_ID_FIELDS[lg.RELATIONSHIP_SPECS[relation][1]]
                       for key, relation in POLICY_NEIGHBOR_SPECS}


def _read(runner, query: str, parameters: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        return runner.read_rows(query, parameters)
    except Exception as error:
        converted = _neo4j_error(error)
        if converted is None:
            raise                      # 쿼리 문법 오류·KeyError 등 코드 문제는 원래 예외 그대로
        raise converted from error


def _neighbor_items(items: list[dict[str, Any]] | None, id_field: str) -> list[dict[str, Any]]:
    flat = [{**(item.get("node") or {}), "relationship": item.get("relationship") or {}} for item in items or []]
    return sorted(flat, key=lambda item: str(item.get(id_field)))


def _build_condition_tree(policy_id: str, root_id: str, groups: dict[str, dict[str, Any]],
                          root_relationship: dict[str, Any], issues: list[dict[str, Any]]) -> dict[str, Any]:
    """그룹 행들(group_id → 행)을 트리로 조립한다. logic 값은 바꾸지 않는다. 순환·미조회 하위 그룹은 issues에 적는다."""

    def build(group_id: str, relationship: dict[str, Any], path: tuple[str, ...]) -> dict[str, Any]:
        if group_id in path:
            issues.append({"issue": "cycle", "group_id": group_id, "path": list(path)})
            return {"group_id": group_id, "cycle": True, "relationship": relationship}
        row = groups.get(group_id)
        if row is None:
            issues.append({"issue": "child_group_not_loaded", "group_id": group_id,
                           "reason": f"최대 깊이 {MAX_CONDITION_DEPTH} 초과 또는 조회 누락"})
            return {"group_id": group_id, "not_loaded": True, "relationship": relationship}
        group = row["group"] or {}
        if group.get("policy_id") != policy_id:
            issues.append({"issue": "group_policy_mismatch", "group_id": group_id,
                           "group_policy_id": group.get("policy_id"), "policy_id": policy_id})
        conditions = sorted(({**(item.get("condition") or {}), "relationship": item.get("relationship") or {}}
                             for item in row["conditions"] or []), key=lambda item: str(item.get("condition_id")))
        children = sorted(row["child_groups"] or [], key=lambda item: str(item.get("group_id")))
        return {
            "group_id": group_id,
            "logic": group.get("logic"),               # AND / OR / NOT / UNKNOWN — 그대로
            **{key: group[key] for key in ("review_status", "verification_status", "eligibility_review_status")
               if key in group},
            "is_root": group.get("is_root"),
            "description": group.get("description"),
            "relationship": relationship,
            "conditions": conditions,
            "groups": [build(child["group_id"], child.get("relationship") or {}, path + (group_id,))
                       for child in children],
        }

    return build(root_id, root_relationship, ())


def _tree_condition_ids(tree: dict[str, Any]) -> set[str]:
    ids = {item["condition_id"] for item in tree.get("conditions", []) if item.get("condition_id")}
    for child in tree.get("groups", []):
        ids |= _tree_condition_ids(child)
    return ids


def _tree_groups(tree: dict[str, Any]) -> list[dict[str, Any]]:
    found = [tree]
    for child in tree.get("groups", []):
        found.extend(_tree_groups(child))
    return found


def get_policy_graph_context(policy_ids: list[str], *, runner) -> dict[str, Any]:
    """
    정책 ID 목록의 그래프 문맥을 Neo4j에서 읽는다. (읽기 전용 세션, 파라미터화된 Cypher 2개)
    입력: policy_ids (데이터 계약 형식, 최대 MAX_POLICY_IDS개), runner (load_graph.Neo4jDriverRunner 등 read_rows 제공 객체)
    반환
      {"requested_policy_ids": [...], "missing_policy_ids": [...],
       "policies": {policy_id: {
           "policy_id", "policy": {Policy 속성},
           "organizations" | "regions" | "categories" | "target_groups" | "life_stages" | "documents":
               [{노드 속성..., "relationship": {target_name, evidence_document_id, evidence_location, review_status}}],
           "modifies_condition_of", "condition_modified_by",
           "eligibility": {"evaluated": False, "note", "condition_tree": [루트 그룹 트리],
                           "requires_condition_ids", "tree_condition_ids", "tree_matches_requires_condition",
                           "unknown_logic_groups", "structure_issues"}}}}
    Neo4j에 없는 정책은 policies에 넣지 않고 missing_policy_ids에 적는다 (만들어 내지 않는다).
    """
    ids = validate_policy_ids(policy_ids)
    if not ids:
        return {"requested_policy_ids": [], "missing_policy_ids": [], "policies": {}}
    base_rows = _read(runner, POLICY_CONTEXT_QUERY, {"policy_ids": ids})
    tree_rows = _read(runner, CONDITION_TREE_QUERY, {"policy_ids": ids})

    groups_by_policy: dict[str, dict[str, dict[str, Any]]] = {}
    for row in tree_rows:
        groups_by_policy.setdefault(row["policy_id"], {})[row["group_id"]] = row

    policies: dict[str, dict[str, Any]] = {}
    for row in base_rows:
        policy_id = row["policy_id"]
        issues: list[dict[str, Any]] = []
        groups = groups_by_policy.get(policy_id, {})
        trees = [_build_condition_tree(policy_id, root["group_id"], groups, root.get("relationship") or {}, issues)
                 for root in sorted(row["root_groups"] or [], key=lambda item: str(item.get("group_id")))]
        if not trees:
            issues.append({"issue": "no_root_condition_group"})
        tree_ids = set().union(*(_tree_condition_ids(tree) for tree in trees)) if trees else set()
        requires = sorted(set(row["requires_condition_ids"] or []))
        context = {"policy_id": policy_id, "policy": row["policy"] or {}}
        for key, _relation in POLICY_NEIGHBOR_SPECS:
            context[key] = _neighbor_items(row[key], _NEIGHBOR_ID_FIELDS[key])
        context["modifies_condition_of"] = sorted(row["modifies_condition_of"] or [], key=lambda i: i["policy_id"])
        context["condition_modified_by"] = sorted(row["condition_modified_by"] or [], key=lambda i: i["policy_id"])
        context["eligibility"] = {
            "evaluated": False,
            "note": ELIGIBILITY_NOTE,
            "condition_tree": trees,
            "requires_condition_ids": requires,
            "tree_condition_ids": sorted(tree_ids),
            # REQUIRES_CONDITION(평탄화 목록)과 트리의 조건 집합이 같은가. 다르면 그래프 인계 데이터 확인 필요
            "tree_matches_requires_condition": set(requires) == tree_ids,
            "unknown_logic_groups": sorted(group["group_id"] for tree in trees for group in _tree_groups(tree)
                                           if group.get("logic") == "UNKNOWN"),
            "structure_issues": issues,
        }
        policies[policy_id] = context
    return {"requested_policy_ids": ids, "missing_policy_ids": [pid for pid in ids if pid not in policies],
            "policies": policies}


# ---------------------------------------------------------------------------
# 4-1. 데이터 품질 경고 (판정·수정하지 않고 표시만)
# ---------------------------------------------------------------------------
def _walk_relationships(tree: dict[str, Any]) -> list[dict[str, Any]]:
    found = [tree.get("relationship") or {}] + [item.get("relationship") or {} for item in tree.get("conditions", [])]
    for child in tree.get("groups", []):
        found.extend(_walk_relationships(child))
    return found


def _annotate(warning: dict[str, Any]) -> dict[str, Any]:
    """경고에 메타 필드를 덧붙인다 (기존 키·값은 그대로). 공식 원문 검증은 하지 않았다."""
    meta = QUALITY_CODE_META.get(warning["code"], {})
    warning.setdefault("severity", meta.get("severity", "medium"))
    warning.setdefault("evidence_level", meta.get("evidence_level", "graph_structure"))
    warning.setdefault("verification", meta.get("verification", "suspected"))
    warning.setdefault("affects", list(meta.get("affects", [])))
    warning.setdefault("blocks_auto_eligibility", meta.get("blocks_auto_eligibility", True))
    warning["official_source_checked"] = False
    return warning


def _graph_quality_patterns(context: dict[str, Any], trees: list[dict[str, Any]], warnings: list[dict[str, Any]]) -> None:
    """그래프만으로 계산하는 추가 의심 패턴 (2026-11-06 Neo4j 의미 점검 S9·S12·검토 상태)."""
    groups = [group for tree in trees for group in _tree_groups(tree)]
    for group in groups:
        if group.get("logic") == "NOT" and not group.get("conditions") and not group.get("groups"):
            warnings.append({"code": QUALITY_EMPTY_NOT_GROUP, "level": "caution", "group_id": group.get("group_id"),
                             "message": ("제외(NOT) 그룹에 평가할 조건이 없음. 제외 대상은 설명 문장·원문에만 있으므로 "
                                         "'제외 조건 없음'으로 해석하지 말 것")})
    policy = context.get("policy") or {}
    organization = str(policy.get("organization") or policy.get("agency") or "")
    region_names = [str(item.get("name")) for item in context.get("regions", [])]
    municipal = [token for token in re.findall(r"([가-힣]+(?:시|군|구))(?=\s|$|/|\))", organization)
                 if token not in region_names and not token.endswith(("특별시", "광역시", "특별자치시"))]
    detail = str(policy.get("region_detail") or "")
    if detail:
        # 시도 관계로 검색되지만 대상은 시군구 한정. 시도 일치만으로 거주 요건 충족으로 보지 않는다.
        warnings.append({"code": QUALITY_REGION_DETAIL, "level": "caution", "region_detail": detail,
                         "graph_regions": region_names,
                         "message": f"지역 대상이 {detail}로 한정됨. 시도 일치만으로 거주 요건 충족 판정 금지"})
        municipal = [token for token in municipal if token not in detail]
    if municipal and region_names:
        warnings.append({"code": QUALITY_REGION_GRANULARITY, "level": "suspected", "organization": organization[:60],
                         "graph_regions": region_names, "municipality_in_organization": municipal[0],
                         "message": (f"운영기관은 {municipal[0]}인데 그래프 지역은 {region_names}(시·도 단위)뿐임. "
                                     "시·군·구 거주 요건이 사라졌을 수 있으니 지역 판정은 원문으로 (확정 아님)")})
    if any(w["code"] in (QUALITY_HOMELESS_PROMOTED, QUALITY_INCOME_ALL_BANDS) for w in warnings):
        verified = sorted({item.get("condition_id") for group in groups for item in group.get("conditions", [])
                           if (item.get("relationship") or {}).get("review_status") == "verified"})
        if verified:
            warnings.append({"code": QUALITY_REVIEW_STATUS_OVERCLAIM, "level": "suspected", "condition_ids": verified,
                             "message": ("생성 규칙 의심 패턴이 있는 정책인데 조건 관계가 review_status=verified로 표기됨. "
                                         "verified를 공식 검증 완료로 해석하지 말 것 (확정 아님)")})


def _apply_static_flags(flags: list[dict[str, Any]], warnings: list[dict[str, Any]]) -> None:
    """정적 의심 목록: 새 경고로 추가하거나, 기존 그래프 경고에 인계 대조 결과(handoff_check)를 덧붙인다."""
    by_code = {warning["code"]: warning for warning in warnings}
    for flag in flags:
        if "enrich" in flag:
            target = by_code.get(flag["enrich"])
            if target is not None:
                target.setdefault("handoff_checks", []).append(
                    {"check": flag["check"], "handoff_semantic_match": flag["handoff_semantic_match"],
                     "evidence_level": flag["evidence_level"], "evidence": flag["evidence"]})
            continue
        warnings.append({"code": flag["code"], "level": "suspected" if flag["evidence_level"] != "source_text_explicit"
                         else "caution", "severity": flag["severity"], "evidence_level": flag["evidence_level"],
                         "verification": flag["handoff_semantic_match"], "affects": flag["affects"],
                         "evidence": flag["evidence"], "source": "static_flags",
                         "message": QUALITY_STATIC_MESSAGES.get(flag["code"], "인계 데이터 의심 (확정 아님)")})


def assess_policy_data_quality(context: dict[str, Any] | None,
                               static_flags: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """
    정책 그래프 문맥에서 데이터 품질 경고를 만든다. (DB 접속 없음, 입력을 바꾸지 않음)
    반환: {"warnings": [{"code", "level", "message", "severity", "evidence_level", "verification", "affects",
                         "blocks_auto_eligibility", "official_source_checked", ...}],
           "review_status_counts", "graph_text_fields",
           "hold_auto_eligibility", "hold_reasons", "affected_attributes", "official_source_checked"}
    level: "caution"(해석 주의) / "suspected"(생성 규칙 오류 의심 패턴 — 확정 아님)   ← 기존 값 그대로
    static_flags: data_quality_flags 목록 중 이 정책 항목 (없으면 그래프 계산만)
    이 함수는 자격을 판정하지 않고, 조건을 VERIFIED로 바꾸거나 틀렸다고 확정하지 않는다.
    hold_auto_eligibility=True는 "자동 판정을 보류하고 원문 근거·추가 질문으로 확인하라"는 뜻이지 부적격이 아니다.
    """
    if context is None:
        warnings = [_annotate({"code": QUALITY_GRAPH_MISSING, "level": "caution",
                               "message": "Neo4j에 이 정책이 없어 그래프 문맥을 붙이지 못함 (정책을 만들어 내지 않음)"})]
        return {"warnings": warnings, "review_status_counts": {}, "graph_text_fields": [],
                **_hold_summary(warnings)}
    eligibility = context.get("eligibility") or {}
    trees = eligibility.get("condition_tree") or []
    warnings: list[dict[str, Any]] = []
    all_groups = [group for tree in trees for group in _tree_groups(tree)]
    unknown_groups = sorted(set(eligibility.get("unknown_logic_groups") or []) |
                            {g["group_id"] for g in all_groups if g.get("logic") == "UNKNOWN" and g.get("group_id")})
    if unknown_groups:
        warnings.append({"code": QUALITY_UNKNOWN_CONDITION, "level": "caution",
                         "group_ids": unknown_groups,
                         "message": DATA_QUALITY_NOTES["unknown"]})
    for group in all_groups:
        if group.get("logic") == "AND" and not group.get("conditions") and not group.get("groups"):
            warnings.append({"code": QUALITY_EMPTY_AND_GROUP, "level": "caution", "group_id": group.get("group_id"),
                             "message": "AND 그룹에 평가할 조건이 없음. 자동 판정 보류 및 원문 확인 필요"})
    if eligibility.get("structure_issues"):
        warnings.append({"code": QUALITY_STRUCTURE_ISSUE, "level": "caution",
                         "issues": [issue.get("issue") for issue in eligibility["structure_issues"]],
                         "message": "조건 트리 구조 이상(순환·미조회 하위 그룹·소속 불일치 등) — 그래프 인계 데이터 확인 필요"})

    relationships = [rel for tree in trees for rel in _walk_relationships(tree)]
    for key, _relation in POLICY_NEIGHBOR_SPECS:
        relationships.extend(item.get("relationship") or {} for item in context.get(key, []))
    review_counts = Counter(str(rel.get("review_status")) for rel in relationships if rel)
    review_objects = [context.get("policy") or {}, *all_groups, *relationships]
    review_objects.extend(condition for group in all_groups for condition in group.get("conditions", []))
    for key, _relation in POLICY_NEIGHBOR_SPECS:
        review_objects.extend(context.get(key, []))
    review_needed = sum(any(item.get(key) == "needs_review" for key in (
        "review_status", "verification_status", "eligibility_review_status"
    )) for item in review_objects)
    if review_needed:
        warnings.append({"code": QUALITY_NEEDS_REVIEW, "level": "caution", "count": review_needed,
                         "message": "정책·조건·그룹·근거에 needs_review 상태가 있어 자동 판정 보류"})
    if review_counts.get("extracted"):
        warnings.append({"code": QUALITY_EXTRACTED_NOT_VERIFIED, "level": "caution",
                         "count": review_counts["extracted"], "message": DATA_QUALITY_NOTES["extracted"]})

    for tree in trees:
        direct = {item.get("condition_id") for item in tree.get("conditions", [])}
        household_or = [group for group in tree.get("groups", []) if group.get("logic") == "OR"
                        and any(str(item.get("condition_id", "")).startswith("COND_HH_") for item in group.get("conditions", []))]
        if tree.get("logic") == "AND" and HOMELESS_CONDITION_ID in direct and household_or:
            warnings.append({"code": QUALITY_HOMELESS_PROMOTED, "level": "suspected", "group_id": tree.get("group_id"),
                             "message": ("무주택이 필수(AND) 조건인데 같은 정책에 가구유형 OR 그룹이 함께 있음. 원천 API의 여러 "
                                         "가구유형 플래그 중 하나가 필수로 승격됐을 가능성 — 공고 원문 확인 필요 (확정 아님)")})
        for group in [tree, *tree.get("groups", [])]:
            ids = {item.get("condition_id") for item in group.get("conditions", [])}
            if group.get("logic") == "OR" and INCOME_BAND_CONDITION_IDS <= ids:
                warnings.append({"code": QUALITY_INCOME_ALL_BANDS, "level": "suspected", "group_id": group.get("group_id"),
                                 "message": ("소득 5구간 전부가 OR로 묶여 있어 의미상 '소득 제한 없음'일 수 있음. "
                                             "소득 조건으로 해석하지 말고 공고 원문 확인 (확정 아님)")})
    _graph_quality_patterns(context, trees, warnings)
    _apply_static_flags(static_flags or [], warnings)
    warnings = [_annotate(warning) for warning in warnings]
    policy = context.get("policy") or {}
    return {"warnings": warnings, "review_status_counts": dict(review_counts),
            "graph_text_fields": [name for name in GRAPH_EXTRACTED_TEXT_FIELDS if policy.get(name)],
            **_hold_summary(warnings)}


def _hold_summary(warnings: list[dict[str, Any]]) -> dict[str, Any]:
    """자동 판정 보류 여부와 이유. 보류는 '부적격'이 아니라 '원문·추가 질문으로 확인 필요'."""
    reasons = sorted({w["code"] for w in warnings if w.get("blocks_auto_eligibility")})
    return {"hold_auto_eligibility": bool(reasons), "hold_reasons": reasons,
            "affected_attributes": sorted({a for w in warnings if w.get("blocks_auto_eligibility") for a in w.get("affects", [])}),
            "official_source_checked": False}


# ---------------------------------------------------------------------------
# 5. 결합
# ---------------------------------------------------------------------------
def combine_policy_evidence(search_result: dict[str, Any], graph_context: dict[str, Any]) -> dict[str, Any]:
    """
    Qdrant 검색 결과와 Neo4j 정책 문맥을 policy_id로 묶는다. (DB 접속 없음)
    - 정책 순서: 그 정책 근거 청크의 가장 좋은 순위(best_rank) 순
    - 정책 안 근거 청크: 검색 순위 순, chunk_id 중복 제거
    - evidence(원문 근거)와 graph(그래프 정보)를 다른 키로 나눈다. graph가 없으면 graph_status="MISSING_IN_GRAPH", graph=None
    - evidence_document_check: 근거 청크의 document_id가 그래프에서 그 정책의 SUPPORTED_BY 문서인지 (PASS/MISMATCH/NOT_TESTED)
    """
    contexts = graph_context.get("policies", {})
    static = dqf.load_flags()                     # manifest가 다르면 적용하지 않는다 (status=stale)
    static_status = dqf.status_summary(static)
    grouped: dict[str, dict[str, Any]] = {}
    unlinked: list[dict[str, Any]] = []
    for hit in sorted(search_result.get("hits", []), key=lambda item: item["rank"]):
        policy_id = hit.get("policy_id")
        if not policy_id:
            unlinked.append(hit)
            continue
        entry = grouped.setdefault(policy_id, {"policy_id": policy_id, "policy_name": hit.get("policy_name"),
                                               "best_rank": hit["rank"], "best_score": hit["score"],
                                               "evidence": [], "_chunk_ids": set()})
        if hit.get("chunk_id") in entry["_chunk_ids"]:
            continue
        entry["_chunk_ids"].add(hit.get("chunk_id"))
        entry["evidence"].append(hit)

    policies = []
    for entry in sorted(grouped.values(), key=lambda item: item["best_rank"]):
        entry.pop("_chunk_ids")
        context = contexts.get(entry["policy_id"])
        entry["graph_status"] = "FOUND" if context is not None else "MISSING_IN_GRAPH"
        entry["graph"] = context
        linked_documents = {doc.get("document_id") for doc in (context or {}).get("documents", [])}
        entry["evidence_document_check"] = {
            hit["document_id"]: (NOT_TESTED if context is None else PASS if hit["document_id"] in linked_documents
                                 else MISMATCH)
            for hit in entry["evidence"]}
        entry["data_quality"] = assess_policy_data_quality(        # 추가 키 (기존 키·순서는 그대로)
            context, static["flags_by_policy"].get(entry["policy_id"]) if context is not None else None)
        if search_result.get("region_search") is not None:
            matches = [hit["region_match"] for hit in entry["evidence"] if "region_match" in hit]
            entry["region_search"] = {
                "matches": matches, "graph_available_in": list((context or {}).get("regions", [])),
                "graph_usage": "post_retrieval_evidence_only", "is_eligibility_decision": False,
                "review_required": any(match["review_required"] for match in matches),
            }
            if entry["region_search"]["review_required"]:
                entry["data_quality"]["warnings"].append(_annotate({
                    "code": QUALITY_SEARCH_REGION, "level": "caution", "message": rs.REVIEW_NOTE,
                    "stored_regions": [match["original_region"] for match in matches],
                }))
                entry["data_quality"].update(_hold_summary(entry["data_quality"]["warnings"]))
        policies.append(entry)
    quality_summary = Counter(warning["code"] for entry in policies for warning in entry["data_quality"]["warnings"])
    return {"query": search_result.get("query"), "filters": search_result.get("filters"),
            **({"region_search": search_result["region_search"]} if "region_search" in search_result else {}),
            "k": search_result.get("k"), "collection": search_result.get("collection"),
            "query_template": search_result.get("query_template"),
            "policies": policies, "unlinked_hits": unlinked,
            "missing_policy_ids": graph_context.get("missing_policy_ids", []),
            "warnings": list(search_result.get("warnings", [])) + (
                [] if static_status["status"] == "applied" else
                [f"정적 데이터 품질 목록 미적용({static_status['status']}): 원문·인계 속성 대조 경고가 빠져 있음"]),
            "latency_seconds": dict(search_result.get("latency_seconds", {})),
            "data_quality_summary": dict(quality_summary),
            "notes": {"score": SCORE_NOTE, "eligibility": ELIGIBILITY_NOTE, **DATA_QUALITY_NOTES},
            # 2026-11-06 추가 필드 (기존 키는 그대로)
            "search_profile": search_result.get("search_profile"),
            "hold_auto_eligibility_policy_ids": [p["policy_id"] for p in policies
                                                 if p["data_quality"].get("hold_auto_eligibility")],
            "static_quality_flags": static_status}


# ---------------------------------------------------------------------------
# 6. 통합 진입 함수
# ---------------------------------------------------------------------------
@contextmanager
def open_neo4j_runner(*, connection_timeout: float = NEO4J_CONNECTION_TIMEOUT_SECONDS,
                      acquisition_timeout: float = NEO4J_ACQUISITION_TIMEOUT_SECONDS,
                      query_timeout: float | None = NEO4J_QUERY_TIMEOUT_SECONDS) -> Iterator[ReadOnlyTimedRunner]:
    """
    .env의 NEO4J_URI·NEO4J_USER·NEO4J_PASSWORD로 드라이버를 열고 읽기 전용 실행기를 준다. with 블록이 끝나면(예외 포함) 닫는다.
    연결 확인 실패·설정 오류는 HybridSearchBackendError(backend="neo4j"). 값은 출력하지 않는다.
    """
    driver = _make_neo4j_driver(connection_timeout, acquisition_timeout)
    try:
        yield ReadOnlyTimedRunner(driver, query_timeout=query_timeout)
    finally:
        _close_quietly(driver)


class HybridSearchSession:
    """
    반복 요청용 연결 묶음. Qdrant client·Neo4j 드라이버를 한 번 만들어 여러 질문에 재사용한다.

        with HybridSearchSession() as session:            # 연결은 처음 쓸 때 만든다
            result = session.search_policy_context("질문", k=5)
            result2 = session.search_policy_context("다른 질문", filters={"document_id": "DOC_LH_001"})
        # with가 끝나면(예외가 나도) 세션이 "직접 만든" 연결만 닫는다

    client·runner를 넘기면 그 객체는 세션이 닫지 않는다 (소유권은 넘긴 쪽).
    검색 설정: profile(기본 DEFAULT_PROFILE) 또는 collection 직접 지정 — resolve_search_settings 참고.
    설정은 세션을 만들 때 한 번 검증한다 (잘못된 조합은 연결을 열기 전에 SearchProfileError).
    """

    def __init__(self, *, client=None, runner=None, embedder: em.Embedder | None = None,
                 collection: str | None = None, query_template: Any = _UNSET, profile: str | None = None,
                 qdrant_timeout: int = QDRANT_TIMEOUT_SECONDS,
                 neo4j_connection_timeout: float = NEO4J_CONNECTION_TIMEOUT_SECONDS,
                 neo4j_acquisition_timeout: float = NEO4J_ACQUISITION_TIMEOUT_SECONDS,
                 neo4j_query_timeout: float | None = NEO4J_QUERY_TIMEOUT_SECONDS):
        self.settings = resolve_search_settings(profile=profile, collection=collection, embedder=embedder,
                                                query_template=query_template)
        self._client, self._owns_client = client, client is None
        self._runner, self._owns_runner = runner, runner is None
        self._driver = None
        self.embedder = embedder
        self.collection = self.settings["collection"]
        self.query_template = self.settings["query_template"]
        self.profile = self.settings["name"]
        self._qdrant_timeout = qdrant_timeout
        self._neo4j_settings = (neo4j_connection_timeout, neo4j_acquisition_timeout, neo4j_query_timeout)
        self.closed = False
        self.close_errors: list[str] = []
        self._vector_size_cache = None
        self._region_catalog_cache = None

    # --- 연결 (처음 쓸 때 만든다) ---
    def _check_open(self) -> None:
        if self.closed:
            raise RuntimeError("이미 닫힌 HybridSearchSession입니다.")

    @property
    def client(self):
        self._check_open()
        if self._client is None:
            self._client = make_qdrant_client(self._qdrant_timeout)
        return self._client

    @property
    def runner(self):
        self._check_open()
        if self._runner is None:
            connection_timeout, acquisition_timeout, query_timeout = self._neo4j_settings
            self._driver = _make_neo4j_driver(connection_timeout, acquisition_timeout)
            self._runner = ReadOnlyTimedRunner(self._driver, query_timeout=query_timeout)
        return self._runner

    @property
    def owned_resources(self) -> dict[str, bool]:
        """세션이 직접 만들어 닫을 책임이 있는 연결이 지금 열려 있는가 (테스트·점검용)."""
        return {"qdrant_client": self._owns_client and self._client is not None,
                "neo4j_driver": self._owns_runner and self._driver is not None}

    def close(self) -> None:
        """세션이 만든 연결만 닫는다. 여러 번 불러도 된다. 닫기 오류는 close_errors에 남기고 올리지 않는다."""
        if self.closed:
            return
        self.closed = True
        if self._owns_client:
            error = _close_quietly(self._client)
            if error is not None:
                self.close_errors.append(f"qdrant: {_safe_message(error)}")
            self._client = None
        if self._owns_runner:
            error = _close_quietly(self._driver)
            if error is not None:
                self.close_errors.append(f"neo4j: {_safe_message(error)}")
            self._driver, self._runner = None, None

    def __enter__(self) -> "HybridSearchSession":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    # --- 검색 (모듈 함수와 같은 입력·출력) ---
    def search_policy_evidence(self, query: str, filters: dict[str, Any] | None = None, k: int = DEFAULT_K) -> dict[str, Any]:
        query, filters, k = validate_query(query), validate_filters(filters), validate_k(k)
        client = self.client
        collection = self.settings["collection"]
        now = time.monotonic()
        cached = self._vector_size_cache
        def refresh_vector_size() -> int:
            size = _collection_vector_size(client, collection)
            self._vector_size_cache = (collection, time.monotonic() + 30, size)
            return size

        cache_hit = cached is not None and cached[0] == collection and now < cached[1]
        vector_size = cached[2] if cache_hit else refresh_vector_size()
        catalog = None
        version = hd.dataset_version()
        if version == "1.4.0-candidate":
            path = hd.PAYLOAD_CHUNKS_PATH
            key = (version, path, hd.file_signature(path))
            if self._region_catalog_cache is None or self._region_catalog_cache[0] != key:
                self._region_catalog_cache = (key, rs.build_region_catalog(hd.load_payload_chunks()))
            catalog = self._region_catalog_cache[1]
        try:
            return _search_evidence(client, query, filters, k, self.settings,
                                    vector_size=vector_size, region_catalog=catalog,
                                    refresh_vector_size=refresh_vector_size if cache_hit else None)
        except (SearchProfileError, HybridSearchBackendError):
            self._vector_size_cache = None
            raise

    def get_policy_graph_context(self, policy_ids: list[str]) -> dict[str, Any]:
        ids = validate_policy_ids(policy_ids)
        return get_policy_graph_context(ids, runner=self.runner) if ids else get_policy_graph_context([], runner=None)

    def search_policy_context(self, query: str, filters: dict[str, Any] | None = None, k: int = DEFAULT_K) -> dict[str, Any]:
        search = self.search_policy_evidence(query, filters, k)
        ordered_ids, invalid = _collect_policy_ids(search["hits"])
        started = time.perf_counter()
        context = get_policy_graph_context(ordered_ids, runner=self.runner if ordered_ids else None)
        result = combine_policy_evidence(search, context)
        result["latency_seconds"]["neo4j_context"] = round(time.perf_counter() - started, 4)
        result["invalid_policy_ids"] = invalid
        return result

    def verify_cross_db(self) -> dict[str, Any]:
        return verify_cross_db(client=self.client, runner=self.runner, collection=self.collection)


def _collect_policy_ids(hits: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    """검색 순위 순서로 policy_id를 모은다(중복 제거). 형식이 틀린 ID는 Neo4j에 보내지 않고 따로 모은다."""
    ordered_ids: list[str] = []
    invalid: list[str] = []
    for hit in hits:
        policy_id = hit.get("policy_id")
        if not policy_id or policy_id in ordered_ids or policy_id in invalid:
            continue
        (ordered_ids if POLICY_ID_PATTERN.match(policy_id) else invalid).append(policy_id)
    return ordered_ids, invalid


def search_policy_context(query: str, filters: dict[str, Any] | None = None, k: int = DEFAULT_K, *,
                          client=None, runner=None, embedder: em.Embedder | None = None,
                          collection: str | None = None, query_template: Any = _UNSET,
                          profile: str | None = None) -> dict[str, Any]:
    """
    통합 진입 함수: 질의 임베딩 → Qdrant Top-K → policy_id 수집 → Neo4j 정책 문맥 → 결합.
    - 기존 호출 search_policy_context(question, filters=None, k=5)는 그대로 동작한다.
      collection을 주지 않으면 기본 프로필(DEFAULT_PROFILE = 0.6B 전체 색인)로 검색한다.
    - client·runner를 넘기면 그대로 쓰고 닫지 않는다.
    - 넘기지 않으면 이 호출 안에서 만들고, 끝나면(예외 포함) 닫는다. 질문을 여러 번 보낼 때는 HybridSearchSession을 쓰면
      연결을 재사용해 빠르다.
    반환: combine_policy_evidence 결과 + latency_seconds["neo4j_context"], "invalid_policy_ids"
    """
    validate_query(query), validate_filters(filters), validate_k(k)   # 연결을 만들기 전에 입력부터 확인
    with HybridSearchSession(client=client, runner=runner, embedder=embedder, collection=collection,
                             query_template=query_template, profile=profile) as session:
        return session.search_policy_context(query, filters, k)


# ---------------------------------------------------------------------------
# 7. Qdrant ↔ Neo4j 교차 검증 (읽기 전용)
# ---------------------------------------------------------------------------
CROSS_POLICY_QUERY = ("UNWIND $policy_ids AS policy_id\n"
                      "OPTIONAL MATCH (p:Policy {policy_id: policy_id})\n"
                      "RETURN policy_id, p IS NOT NULL AS exists")
CROSS_DOCUMENT_QUERY = ("UNWIND $document_ids AS document_id\n"
                        "OPTIONAL MATCH (d:Document {document_id: document_id})\n"
                        "OPTIONAL MATCH (p:Policy)-[:SUPPORTED_BY]->(d)\n"
                        "RETURN document_id, d IS NOT NULL AS exists, d.content_hash AS content_hash,\n"
                        "       d.policy_id AS policy_id, [x IN collect(p.policy_id) WHERE x IS NOT NULL] AS supported_by")
CROSS_PAYLOAD_FIELDS = ("chunk_id", "point_id", "policy_id", "document_id", "content_hash")
CROSS_CHECKS = ("point_id", "chunk_id_document", "policy_exists", "document_exists", "supported_by",
                "document_policy_id", "content_hash")


def read_payload_keys(client, collection: str = DEFAULT_COLLECTION, page_size: int = 256) -> list[dict[str, Any]]:
    """Qdrant 컬렉션 전체 point의 연결 키(CROSS_PAYLOAD_FIELDS)와 point id를 읽는다. (scroll, 읽기 전용)"""
    store.assert_experiment_collection(collection)
    rows, offset = [], None
    while True:
        records, offset = client.scroll(collection_name=collection, limit=page_size, offset=offset,
                                        with_payload=list(CROSS_PAYLOAD_FIELDS), with_vectors=False)
        rows.extend({"id": str(record.id), **{field: (record.payload or {}).get(field) for field in CROSS_PAYLOAD_FIELDS}}
                    for record in records)
        if offset is None:
            return rows


def _check_chunk(row: dict[str, Any], policies: dict[str, bool], documents: dict[str, dict[str, Any]]) -> dict[str, str]:
    policy_id, document_id = row["policy_id"], row["document_id"]
    document = documents.get(document_id) or {}
    document_found = bool(document.get("exists"))
    policy_found = bool(policies.get(policy_id))
    result = {
        "point_id": PASS if row["chunk_id"] and row["id"] == make_point_id(row["chunk_id"]) == row["point_id"]
        else MISMATCH,
        "policy_exists": PASS if policy_found else MISSING,
        "document_exists": PASS if document_found else MISSING,
    }
    match = B_CHUNK_ID_PATTERN.match(row["chunk_id"] or "")
    result["chunk_id_document"] = (NOT_TESTED if not match else PASS if match.group(1) == document_id else MISMATCH)
    if not (document_found and policy_found):
        result["supported_by"] = NOT_TESTED
    elif policy_id in (document.get("supported_by") or []):
        result["supported_by"] = PASS
    else:
        result["supported_by"] = MISMATCH if document.get("supported_by") else MISSING
    result["document_policy_id"] = (NOT_TESTED if not document_found
                                    else PASS if document.get("policy_id") == policy_id else MISMATCH)
    if not document_found:
        result["content_hash"] = NOT_TESTED
    elif not row["content_hash"] or not document.get("content_hash"):
        result["content_hash"] = MISSING
    else:
        result["content_hash"] = PASS if row["content_hash"] == document["content_hash"] else MISMATCH
    return result


def verify_cross_db(*, client, runner, collection: str = DEFAULT_COLLECTION) -> dict[str, Any]:
    """
    Qdrant point(청크)마다 Neo4j와 연결이 맞는지 확인한다. (두 DB 모두 읽기 전용)
    검사 항목 (CROSS_CHECKS) — 각 결과는 PASS / MISSING / MISMATCH / NOT_TESTED
      point_id           : Qdrant id == payload point_id == uuid5(chunk_id)
      chunk_id_document  : chunk_id(CHK_{버전}_{문서}_C…) 안의 문서 ID == payload document_id (형식이 다르면 NOT_TESTED)
      policy_exists      : payload policy_id인 Policy 노드가 있는가
      document_exists    : payload document_id인 Document 노드가 있는가
      supported_by       : (Policy {payload policy_id})-[:SUPPORTED_BY]->(Document {payload document_id})가 있는가
                           (없으면 MISSING, 다른 정책에만 연결되어 있으면 MISMATCH = 청크가 잘못된 정책에 붙음)
      document_policy_id : Document 노드의 policy_id == payload policy_id
      content_hash       : Document 노드의 content_hash == payload content_hash
    반환: {"status", "qdrant_points", "qdrant_count", "summary": {검사: {상태: 수}}, "failures": [...], "by_document": {...}}
    """
    try:
        rows = read_payload_keys(client, collection)
        qdrant_count = store.count_points(client, collection)
    except Exception as error:
        converted = _qdrant_error(error)
        if converted is None:
            raise                       # 컬렉션 보호 오류·코드 버그는 원래 예외 그대로
        raise converted from error
    policy_ids = sorted({row["policy_id"] for row in rows if row["policy_id"]})
    document_ids = sorted({row["document_id"] for row in rows if row["document_id"]})
    policies = {row["policy_id"]: row["exists"] for row in _read(runner, CROSS_POLICY_QUERY, {"policy_ids": policy_ids})}
    documents = {row["document_id"]: row for row in _read(runner, CROSS_DOCUMENT_QUERY, {"document_ids": document_ids})}

    summary = {check: {PASS: 0, MISSING: 0, MISMATCH: 0, NOT_TESTED: 0} for check in CROSS_CHECKS}
    failures, by_document = [], {}
    for row in rows:
        checks = _check_chunk(row, policies, documents)
        for check, status in checks.items():
            summary[check][status] += 1
        doc = by_document.setdefault(row["document_id"], {"chunks": 0, "policy_ids": set(), "all_pass": True})
        doc["chunks"] += 1
        doc["policy_ids"].add(row["policy_id"])
        if any(status != PASS for status in checks.values()):
            doc["all_pass"] = False
            failures.append({"chunk_id": row["chunk_id"], "policy_id": row["policy_id"],
                             "document_id": row["document_id"],
                             "checks": {check: status for check, status in checks.items() if status != PASS}})
    for doc in by_document.values():
        doc["policy_ids"] = sorted(doc["policy_ids"])
    complete = len(rows) == qdrant_count
    return {"status": PASS if not failures and complete and rows else MISMATCH if rows else NOT_TESTED,
            "collection": collection, "qdrant_points": len(rows), "qdrant_count": qdrant_count,
            "all_points_read": complete, "policy_ids": policy_ids, "document_ids": document_ids,
            "summary": summary, "failures": failures, "by_document": by_document}
