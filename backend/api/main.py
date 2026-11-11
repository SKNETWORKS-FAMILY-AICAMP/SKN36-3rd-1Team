"""질문 API와 프로세스별 Hybrid 검색 세션을 관리한다."""

from collections.abc import Callable
from contextlib import asynccontextmanager
import logging
import os
from threading import Lock

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.api.schemas import AskRequest, AskResponse
from backend.rag.pipeline import AskError, run_ask_pipeline
from database import embedding
# handoff_data는 hybrid_search import 중 데이터셋 경로를 고정하므로 먼저 환경을 읽는다.
embedding.load_env_file()
if os.environ.get("LIFEPOLICY_DATASET_DIR") == "":
    os.environ.pop("LIFEPOLICY_DATASET_DIR")
from database.hybrid_search import (
    HybridSearchBackendError,
    HybridSearchInputError,
    HybridSearchSession,
    SearchProfileError,
)

logger = logging.getLogger(__name__)
ERRORS = {
    "SEARCH_BACKEND_UNAVAILABLE": (503, "정책 검색 서비스를 잠시 이용할 수 없습니다."),
    "SEARCH_TIMEOUT": (504, "정책 검색 시간이 초과되었습니다."),
    "LLM_UNAVAILABLE": (502, "답변 생성 서비스를 잠시 이용할 수 없습니다."),
    "LLM_TIMEOUT": (504, "답변 생성 시간이 초과되었습니다."),
    "LLM_OUTPUT_INVALID": (502, "답변 또는 출처를 검증할 수 없습니다."),
    "ELIGIBILITY_OUTPUT_INVALID": (502, "조건 판정 결과를 검증할 수 없습니다."),
    "INVALID_SEARCH_INPUT": (422, "정책 검색 입력을 확인해 주세요."),
    "INTERNAL_ERROR": (500, "요청 처리 중 오류가 발생했습니다."),
}


def _new_search_session() -> HybridSearchSession:
    embedding.load_env_file()
    session = HybridSearchSession()
    logger.info("검색 세션 설정: profile=%s collection=%s", session.profile, session.collection)
    return session


def _check_eligibility(profile: dict, policy: dict) -> dict:
    try:
        from backend.eligibility.checker import check_eligibility
    except ModuleNotFoundError as exc:
        if exc.name != "backend.eligibility.checker":
            raise
        raise AskError("ELIGIBILITY_OUTPUT_INVALID") from exc
    return check_eligibility(profile, policy)


def _generate_answer(
    question: str, contexts: list[dict], checks: list[dict], history: list[dict], profile: dict | None = None,
) -> dict:
    """Pipeline이 정규화한 프로필(age/region/income)을 Generator에 키워드 인자로 넘긴다.

    Generator가 profile 인자를 지원해야 한다(#35). 오류를 숨기는 재호출은 하지 않는다.
    """
    try:
        from backend.llm.generator import generate_answer
    except ModuleNotFoundError as exc:
        if exc.name not in {"backend.llm", "backend.llm.generator"}:
            raise
        raise AskError("LLM_UNAVAILABLE") from exc
    return generate_answer(question, contexts, checks, history, profile=profile)


def create_app(
    *,
    session_factory: Callable[[], HybridSearchSession] = _new_search_session,
    check_fn: Callable = _check_eligibility,
    generate_fn: Callable = _generate_answer,
    display_fn: Callable[[list[dict]], list[dict]] | None = None,
) -> FastAPI:
    """서버 수명 동안 검색 세션을 재사용한다. 검색 호출만 직렬화한다."""
    search_lock = Lock()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        application.state.search_ready = False
        application.state.session = None
        try:
            application.state.session = session_factory()
        except (SearchProfileError, HybridSearchBackendError, OSError) as exc:
            logger.warning("검색 세션 초기화 실패: %s", type(exc).__name__)
        try:
            yield
        finally:
            session = application.state.session
            if session is not None:
                session.close()
            application.state.search_ready = False

    application = FastAPI(title="SSOK", lifespan=lifespan)
    application.add_middleware(
        CORSMiddleware, allow_origins=["http://127.0.0.1:5173"],
        allow_methods=["POST", "GET"], allow_headers=["Content-Type"],
    )

    @application.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Pydantic 기본 오류의 input 및 임의 키를 반환하면 사용자 정보가 노출될 수 있다.
        return JSONResponse(status_code=422, content={
            "detail": {"code": "INVALID_REQUEST", "message": "질문 및 프로필 입력 형식을 확인해 주세요."},
        })

    def search(question: str, *, filters: dict | None, k: int) -> dict:
        with search_lock:
            session = application.state.session
            if session is None:
                raise AskError("SEARCH_BACKEND_UNAVAILABLE")
            try:
                result = session.search_policy_context(question, filters=filters, k=k)
            except SearchProfileError as exc:
                application.state.search_ready = False
                raise AskError("SEARCH_BACKEND_UNAVAILABLE") from exc
            except HybridSearchInputError as exc:
                raise AskError("INVALID_SEARCH_INPUT") from exc
            except (HybridSearchBackendError, TimeoutError, ConnectionError) as exc:
                application.state.search_ready = False
                code = ("SEARCH_TIMEOUT" if isinstance(exc, TimeoutError) or getattr(exc, "kind", None) == "timeout"
                        else "SEARCH_BACKEND_UNAVAILABLE")
                raise AskError(code) from exc
            # 빈 검색은 Neo4j 호출을 생략할 수 있다.
            if result["policies"]:
                application.state.search_ready = True
            return result

    @application.get("/health")
    def health() -> dict:
        return {"status": "ok", "ready": getattr(application.state, "search_ready", False)}

    @application.post("/api/ask", response_model=AskResponse)
    def ask(request: AskRequest) -> AskResponse:
        try:
            return run_ask_pipeline(request, search_fn=search, check_fn=check_fn, generate_fn=generate_fn,
                                    display_fn=display_fn)
        except AskError as exc:
            status, message = ERRORS[exc.code]
            if status >= 500:
                logger.warning("질문 요청 실패: %s", exc.code)
            raise HTTPException(status, detail={"code": exc.code, "message": message}) from exc
        except Exception as exc:
            logger.error("예기치 않은 질문 처리 오류: %s", type(exc).__name__)
            status, message = ERRORS["INTERNAL_ERROR"]
            raise HTTPException(status, detail={"code": "INTERNAL_ERROR", "message": message}) from exc

    return application


app = create_app()
