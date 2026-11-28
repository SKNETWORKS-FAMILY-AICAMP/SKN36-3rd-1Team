"""질문 요청 하나의 전체 마감 시각.

화면(frontend/src/config/request.ts ASK_TIMEOUT_MS)은 60초가 지나면 요청을 포기한다.
검색(분야 보충 검색 포함)과 LLM 호출·재시도가 각자 제한 시간을 지켜도 합치면 60초를 넘을 수 있어,
요청 시작 시각 기준 남은 시간을 한곳에서 계산해 보충 검색·LLM 재시도를 줄인다.

마감 시각은 contextvar에 둔다. FastAPI 동기 엔드포인트는 요청마다 한 스레드에서 Pipeline을 끝까지 실행하므로
다른 요청과 섞이지 않는다. 마감이 설정되지 않은 호출(단위 테스트에서 Generator를 직접 부르는 경우 등)은
기존 동작을 그대로 따른다.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

# 화면 제한 60초에서 응답 직렬화·네트워크 여유 5초를 뺀 값.
ASK_BUDGET_SECONDS = 55.0

_deadline: ContextVar[float | None] = ContextVar("ask_deadline", default=None)


@contextmanager
def ask_deadline(budget_seconds: float = ASK_BUDGET_SECONDS) -> Iterator[None]:
    """이 블록 안의 호출이 같은 마감 시각을 보게 한다. 바깥에 이미 마감이 있으면 더 이른 쪽을 쓴다."""
    outer = _deadline.get()
    deadline = time.monotonic() + budget_seconds
    if outer is not None:
        deadline = min(deadline, outer)
    token = _deadline.set(deadline)
    try:
        yield
    finally:
        _deadline.reset(token)


def remaining_seconds() -> float | None:
    """남은 시간(초). 마감이 설정되지 않았으면 None."""
    deadline = _deadline.get()
    if deadline is None:
        return None
    return deadline - time.monotonic()
