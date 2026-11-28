"""#165: LLM 공급자 오류를 종류별로 나눠 처리한다. 실제 API 요청은 보내지 않는다(가짜 SDK).

- 429 요청 한도 초과: 다시 보내지 않고 LLMRateLimitedError → API 503 LLM_RATE_LIMITED
- 5xx·연결 실패(일시 장애): 한 번만 다시 보낸다. 또 실패하면 LLMUnavailableError
- 401 등 그 밖의 상태 오류: 다시 보내지 않고 LLMUnavailableError
- 시간 초과: 다시 보내지 않고 LLMTimeoutError
"""

import sys
from types import ModuleType, SimpleNamespace

import pytest

from backend.api.main import ERRORS
from backend.api.schemas import AskRequest
from backend.llm import generator as g
from backend.rag.pipeline import AskError, _generate_output


@pytest.fixture
def fake_sdk(monkeypatch):
    sdk = ModuleType("openai")

    class OpenAIError(Exception):
        pass

    class APIStatusError(OpenAIError):
        def __init__(self, status_code):
            super().__init__(f"HTTP {status_code}")
            self.status_code = status_code

    class APIConnectionError(OpenAIError):
        pass

    class APITimeoutError(APIConnectionError):
        pass

    sdk.OpenAIError, sdk.APIStatusError = OpenAIError, APIStatusError
    sdk.APIConnectionError, sdk.APITimeoutError = APIConnectionError, APITimeoutError
    state = {"errors": [], "calls": 0}

    def create(**kwargs):
        state["calls"] += 1
        if state["errors"]:
            raise state["errors"].pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content="{}"))])

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))

    sdk.OpenAI = FakeOpenAI
    monkeypatch.setitem(sys.modules, "openai", sdk)
    monkeypatch.setenv("OPENAI_API_KEY", "unit-test-key-not-real")
    monkeypatch.setattr(g.time, "sleep", lambda seconds: state.setdefault("slept", []).append(seconds))
    g._get_openai_client.cache_clear()
    return SimpleNamespace(sdk=sdk, state=state)


def call():
    return g._call_openai([{"role": "user", "content": "테스트"}])


def test_rate_limit_is_not_retried(fake_sdk):
    fake_sdk.state["errors"] = [fake_sdk.sdk.APIStatusError(429)]
    with pytest.raises(g.LLMRateLimitedError):
        call()
    assert fake_sdk.state["calls"] == 1


@pytest.mark.parametrize("make_error", [lambda sdk: sdk.APIStatusError(503), lambda sdk: sdk.APIConnectionError("offline")])
def test_transient_failure_is_retried_once(fake_sdk, make_error):
    fake_sdk.state["errors"] = [make_error(fake_sdk.sdk)]
    assert call() == "{}"
    assert fake_sdk.state["calls"] == 2
    assert fake_sdk.state["slept"] == [g.TRANSIENT_RETRY_DELAY_SECONDS]


def test_transient_failure_twice_is_unavailable(fake_sdk):
    fake_sdk.state["errors"] = [fake_sdk.sdk.APIStatusError(500), fake_sdk.sdk.APIStatusError(502)]
    with pytest.raises(g.LLMUnavailableError) as raised:
        call()
    assert not isinstance(raised.value, g.LLMRateLimitedError)
    assert fake_sdk.state["calls"] == 2


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_client_errors_are_not_retried(fake_sdk, status):
    fake_sdk.state["errors"] = [fake_sdk.sdk.APIStatusError(status)]
    with pytest.raises(g.LLMUnavailableError):
        call()
    assert fake_sdk.state["calls"] == 1


def test_timeout_is_not_retried(fake_sdk):
    fake_sdk.state["errors"] = [fake_sdk.sdk.APITimeoutError("slow")]
    with pytest.raises(g.LLMTimeoutError):
        call()
    assert fake_sdk.state["calls"] == 1


@pytest.mark.parametrize(("error", "code"), [
    (g.LLMRateLimitedError("x"), "LLM_RATE_LIMITED"),
    (g.LLMUnavailableError("x"), "LLM_UNAVAILABLE"),
    (g.LLMOutputError("x"), "LLM_OUTPUT_INVALID"),
])
def test_pipeline_maps_error_kinds(error, code):
    def failing(*args, **kwargs):
        raise error

    request = AskRequest(question="가상 질문", profile={"age": None, "region": "", "income": None}, history=[])
    with pytest.raises(AskError) as raised:
        _generate_output(request, failing, [], [], {})
    assert raised.value.code == code
    assert code in ERRORS


def test_rate_limited_http_status():
    assert ERRORS["LLM_RATE_LIMITED"][0] == 503
