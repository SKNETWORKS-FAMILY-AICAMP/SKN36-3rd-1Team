"""요청 전체 마감과 답변 줄바꿈 보존 단위 테스트. 실제 API·DB는 호출하지 않는다(가짜 SDK·가짜 검색).

- 중복 입력 요청 문장을 지워도 남은 답변의 문단·목록 줄바꿈이 그대로 남는다.
- 요청 마감(backend/core/deadline.py)이 가까우면 분야 보충 검색(#75)을 건너뛴다.
- 요청 마감이 가까우면 LLM을 새로 호출하지 않고, 일시 장애 재시도도 하지 않는다.
- 마감이 없는 호출은 기존 동작(설정된 timeout, 재시도 1회)을 그대로 따른다.
정책·청크는 가상 값이다(sample_only).
"""

import sys
from types import ModuleType, SimpleNamespace

import pytest

from backend.core import deadline
from backend.llm import generator as g
from backend.rag import pipeline


PROFILE = g._normalize_profile({"age": 30, "region": None, "income": None, "child_age_months": None})


def test_redundant_request_removal_keeps_paragraphs_and_lists():
    answer = "첫 문단입니다 [1].\n\n- 항목 하나 [1]\n- 항목 둘 [1]\n나이를 알려 주세요.\n\n마지막 문단입니다 [1]."
    result = g._remove_redundant_answer_requests(answer, PROFILE)
    assert "나이를 알려" not in result
    assert result == "첫 문단입니다 [1].\n\n- 항목 하나 [1]\n- 항목 둘 [1]\n\n마지막 문단입니다 [1]."


def test_removed_sentence_at_paragraph_end_keeps_paragraph_break():
    answer = "첫 문단입니다 [1]. 나이를 알려 주세요.\n\n다음 문단입니다 [1]."
    result = g._remove_redundant_answer_requests(answer, PROFILE)
    assert result == "첫 문단입니다 [1].\n\n다음 문단입니다 [1]."


def test_removed_sentence_before_inline_sentence_keeps_paragraph_break():
    answer = "첫 문단입니다 [1].\n\n나이를 알려 주세요. 다음 문장입니다 [1]."
    result = g._remove_redundant_answer_requests(answer, PROFILE)
    assert result == "첫 문단입니다 [1].\n\n다음 문장입니다 [1]."


def test_no_deadline_outside_block():
    assert deadline.remaining_seconds() is None
    with deadline.ask_deadline(10):
        remaining = deadline.remaining_seconds()
        assert remaining is not None and 0 < remaining <= 10
    assert deadline.remaining_seconds() is None


def test_nested_deadline_keeps_earlier_one():
    with deadline.ask_deadline(5):
        with deadline.ask_deadline(100):
            assert deadline.remaining_seconds() <= 5


def _policy(pid, rank, word):
    return {"policy_id": pid, "policy_name": f"가상 {pid}", "best_rank": rank, "graph": {"documents": []},
            "evidence": [{"rank": rank, "document_id": f"DOC_{pid}", "chunk_id": f"CHK_{pid}",
                          "text": f"{pid} {word} 원문", "source_url": f"https://example.org/{pid}"}]}


def test_topic_search_is_skipped_near_deadline():
    calls = []

    def search(query, filters=None, k=10):
        calls.append(query)
        return {"policies": [_policy("EDU1", 1, "교육비")]}

    with deadline.ask_deadline(pipeline.TOPIC_SEARCH_MIN_REMAINING_SECONDS - 1):
        found = pipeline.search_policies(search, "교육비·구직·교통 지원 정책이 있나요?", None)
    # 원래 질문 1회만 검색하고 분야 보충 검색은 하지 않는다.
    assert calls == ["교육비·구직·교통 지원 정책이 있나요?"]
    assert [p["policy_id"] for p in found] == ["EDU1"]


@pytest.fixture
def fake_sdk(monkeypatch):
    sdk = ModuleType("openai")

    class OpenAIError(Exception):
        pass

    class APIStatusError(OpenAIError):
        def __init__(self, status_code):
            super().__init__(status_code)
            self.status_code = status_code

    class APIConnectionError(OpenAIError):
        pass

    class APITimeoutError(APIConnectionError):
        pass

    sdk.OpenAIError, sdk.APIStatusError = OpenAIError, APIStatusError
    sdk.APIConnectionError, sdk.APITimeoutError = APIConnectionError, APITimeoutError
    state = {"errors": [], "kwargs": []}

    def create(**kwargs):
        state["kwargs"].append(kwargs)
        if state["errors"]:
            raise state["errors"].pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content="{}"))])

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))

    sdk.OpenAI = FakeOpenAI
    monkeypatch.setitem(sys.modules, "openai", sdk)
    monkeypatch.setenv("OPENAI_API_KEY", "unit-test-key-not-real")
    monkeypatch.delenv("LLM_TIMEOUT_SECONDS", raising=False)
    monkeypatch.setattr(g.time, "sleep", lambda seconds: None)
    g._get_openai_client.cache_clear()
    return SimpleNamespace(sdk=sdk, state=state)


def call():
    return g._call_openai([{"role": "user", "content": "테스트"}])


def test_without_deadline_uses_configured_timeout_and_retries(fake_sdk):
    fake_sdk.state["errors"] = [fake_sdk.sdk.APIStatusError(503)]
    assert call() == "{}"
    assert len(fake_sdk.state["kwargs"]) == 2
    assert all("timeout" not in kwargs for kwargs in fake_sdk.state["kwargs"])


def test_short_deadline_caps_attempt_timeout(fake_sdk):
    with deadline.ask_deadline(12):
        assert call() == "{}"
    timeout = fake_sdk.state["kwargs"][0]["timeout"]
    assert g.MIN_LLM_ATTEMPT_SECONDS <= timeout <= 12 - g.LLM_POST_PROCESS_MARGIN_SECONDS


def test_no_llm_call_when_deadline_is_too_close(fake_sdk):
    with deadline.ask_deadline(g.MIN_LLM_ATTEMPT_SECONDS):
        with pytest.raises(g.LLMTimeoutError):
            call()
    assert fake_sdk.state["kwargs"] == []


def test_transient_failure_is_not_retried_near_deadline(fake_sdk, monkeypatch):
    fake_sdk.state["errors"] = [fake_sdk.sdk.APIStatusError(503)]
    with deadline.ask_deadline(30):
        # 첫 호출이 오래 걸려 남은 시간이 최소 호출 시간보다 짧아진 상황.
        monkeypatch.setattr(g, "remaining_seconds",
                            iter([30.0, g.MIN_LLM_ATTEMPT_SECONDS]).__next__)
        with pytest.raises(g.LLMUnavailableError):
            call()
    assert len(fake_sdk.state["kwargs"]) == 1
