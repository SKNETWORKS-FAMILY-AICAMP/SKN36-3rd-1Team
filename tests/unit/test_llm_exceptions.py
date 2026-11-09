"""LLM SDK 예외·출력 잘림·호출 옵션 회귀 테스트.

OpenAI API 요청을 전혀 보내지 않는다. Fake SDK로 generator의 실제 호출 경로를 검증한다.
기존 test_llm_generator.py는 수정하지 않고 이 파일만 추가한다.
"""

import sys
from types import ModuleType, SimpleNamespace

import pytest

from backend.llm import generator as g


@pytest.fixture
def fake_openai(monkeypatch):
    """API를 호출하는 대신 파라미터를 기록하고 정해진 결과/오류를 반환한다."""
    sdk = ModuleType("openai")

    class OpenAIError(Exception):
        pass

    class APIStatusError(OpenAIError):
        pass

    class APIConnectionError(OpenAIError):
        pass

    class APITimeoutError(APIConnectionError):
        pass

    sdk.OpenAIError = OpenAIError
    sdk.APIStatusError = APIStatusError
    sdk.APIConnectionError = APIConnectionError
    sdk.APITimeoutError = APITimeoutError

    recorded = {"exception": None, "content": '{}', "finish_reason": "stop"}

    def create(**kwargs):
        recorded["request"] = kwargs
        if recorded["exception"] is not None:
            raise recorded["exception"]
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason=recorded["finish_reason"],
                    message=SimpleNamespace(content=recorded["content"]),
                )
            ]
        )

    class FakeOpenAI:
        def __init__(self, **kwargs):
            recorded["client"] = kwargs
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=create)
            )

    sdk.OpenAI = FakeOpenAI
    monkeypatch.setitem(sys.modules, "openai", sdk)
    monkeypatch.setenv("OPENAI_API_KEY", "unit-test-key-not-real")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("LLM_MAX_OUTPUT_TOKENS", raising=False)
    monkeypatch.delenv("LLM_TIMEOUT_SECONDS", raising=False)
    return SimpleNamespace(recorded=recorded, sdk=sdk)


def _invoke():
    return g._call_openai(
        [{"role": "user", "content": "테스트용 메시지"}]
    )


def test_authentication_failure_is_unavailable(fake_openai):
    """401 등 인증 오류(APIStatusError 계열)가 가려지지 않고 서비스 오류로 변환됨."""
    fake_openai.recorded["exception"] = fake_openai.sdk.APIStatusError("401")
    with pytest.raises(g.LLMUnavailableError, match="서비스 호출"):
        _invoke()


def test_network_failure_is_unavailable(fake_openai):
    fake_openai.recorded["exception"] = fake_openai.sdk.APIConnectionError("offline")
    with pytest.raises(g.LLMUnavailableError, match="서비스 호출"):
        _invoke()


def test_sdk_timeout_is_timeout(fake_openai):
    fake_openai.recorded["exception"] = fake_openai.sdk.APITimeoutError("timeout")
    with pytest.raises(g.LLMTimeoutError, match="초과"):
        _invoke()


def test_truncated_output_rejected(fake_openai):
    fake_openai.recorded["finish_reason"] = "length"
    fake_openai.recorded["content"] = '{"status": "answered"}'
    with pytest.raises(g.LLMOutputError, match="불완전"):
        _invoke()


def test_empty_response_rejected(fake_openai):
    fake_openai.recorded["content"] = ""
    with pytest.raises(g.LLMOutputError, match="비어"):
        _invoke()


def test_options_and_default_output_limit(fake_openai):
    fake_openai.recorded["content"] = '{"ok": true}'
    assert _invoke() == '{"ok": true}'
    args = fake_openai.recorded["request"]
    assert args["model"] == "gpt-5.4-mini"
    assert args["temperature"] == 0
    assert args["max_completion_tokens"] == 1000
    assert args["response_format"] == {"type": "json_object"}
    assert fake_openai.recorded["client"] == {
        "api_key": "unit-test-key-not-real",
        "timeout": 25.0,
        "max_retries": 0,
    }


def test_options_respect_environment_override(fake_openai, monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "test-custom-model")
    monkeypatch.setenv("LLM_MAX_OUTPUT_TOKENS", "600")
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "11")
    _invoke()
    assert fake_openai.recorded["request"]["model"] == "test-custom-model"
    assert fake_openai.recorded["request"]["max_completion_tokens"] == 600
    assert fake_openai.recorded["client"]["timeout"] == 11.0


def test_blank_llm_env_values_fall_back_to_defaults(fake_openai, monkeypatch):
    """.env.example에 이름만 추가한 빈 설정은 기본값으로 처리한다."""
    monkeypatch.setenv("LLM_MODEL", "")
    monkeypatch.setenv("LLM_MAX_OUTPUT_TOKENS", "")
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "")
    _invoke()
    assert fake_openai.recorded["request"]["model"] == g.DEFAULT_MODEL
    assert fake_openai.recorded["request"]["max_completion_tokens"] == g.DEFAULT_MAX_OUTPUT_TOKENS
    assert fake_openai.recorded["client"]["timeout"] == g.DEFAULT_TIMEOUT_SECONDS


@pytest.mark.parametrize("timeout", ["nan", "inf", "-inf", "0", "-1"])
def test_invalid_timeout_is_rejected(fake_openai, monkeypatch, timeout):
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", timeout)
    with pytest.raises(ValueError, match="LLM_TIMEOUT_SECONDS"):
        _invoke()
