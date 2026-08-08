from __future__ import annotations

import json
from typing import cast

import httpx
import pytest
from pydantic import ValidationError

from agent_reliability_lab.providers import (
    ChatCompletion,
    ChatMessage,
    ChatRole,
    ChatTool,
    FunctionDefinition,
    OpenAICompatibleProvider,
    ProviderError,
    ProviderErrorCode,
    TokenUsage,
    ToolCall,
    ToolCallFunction,
)
from agent_reliability_lab.providers import openai_compatible as provider_module


def _user_message() -> ChatMessage:
    return ChatMessage(role=ChatRole.USER, content="Diagnose incident INC-7")


def test_completion_sends_deterministic_request_and_parses_usage() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "http://localhost:8000/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer local-secret"
        payload = cast(dict[str, object], json.loads(request.content))
        assert payload == {
            "model": "Qwen/Qwen3-8B",
            "messages": [{"role": "user", "content": "Diagnose incident INC-7"}],
            "temperature": 0.0,
            "seed": 17,
        }
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-1",
                "object": "chat.completion",
                "model": "Qwen/Qwen3-8B",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Inspect metrics."},
                        "finish_reason": "stop",
                        "logprobs": None,
                    }
                ],
                "usage": {
                    "prompt_tokens": 11,
                    "completion_tokens": 4,
                    "total_tokens": 15,
                    "prompt_tokens_details": {"cached_tokens": 0},
                },
            },
        )

    provider = OpenAICompatibleProvider(
        base_url="http://localhost:8000/v1/",
        model="Qwen/Qwen3-8B",
        api_key="local-secret",
        seed=17,
        transport=httpx.MockTransport(handler),
    )
    with provider:
        result = provider.complete([_user_message()])

    assert provider.model == "Qwen/Qwen3-8B"
    assert result.message.content == "Inspect metrics."
    assert result.finish_reason == "stop"
    assert result.usage is not None and result.usage.total_tokens == 15
    assert result.latency_ms >= 0
    assert result.provider_attempts == 1
    assert result.retry_error_codes == ()


def test_configured_tools_and_extra_body_can_be_refined_per_call() -> None:
    observed: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert "Authorization" not in request.headers
        observed.update(cast(dict[str, object], json.loads(request.content)))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {
                                        "name": "get_metrics",
                                        "arguments": '{"service_id":"ranker"}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            },
        )

    configured_tool = ChatTool(
        function=FunctionDefinition(
            name="unused_default",
            parameters={"type": "object", "properties": {}},
        )
    )
    selected_tool = ChatTool(
        function=FunctionDefinition(
            name="get_metrics",
            description="Read service metrics",
            parameters={
                "type": "object",
                "properties": {"service_id": {"type": "string"}},
                "required": ["service_id"],
            },
        )
    )
    provider = OpenAICompatibleProvider(
        base_url="http://127.0.0.1:8000/v1",
        model="local-model",
        seed=None,
        tools=[configured_tool],
        extra_body={"top_p": 0.95, "chat_template_kwargs": {"enable_thinking": False}},
        transport=httpx.MockTransport(handler),
    )
    try:
        result = provider.complete(
            [_user_message()],
            tools=[selected_tool],
            extra_body={"top_p": 0.8},
        )
    finally:
        provider.close()

    assert "seed" not in observed
    assert observed["top_p"] == 0.8
    tools = cast(list[dict[str, object]], observed["tools"])
    function = cast(dict[str, object], tools[0]["function"])
    assert function["name"] == "get_metrics"
    assert result.model == "local-model"
    assert result.message.tool_calls[0].function.name == "get_metrics"
    assert result.usage is None


@pytest.mark.parametrize(
    ("status_code", "expected_retryable"),
    [
        (400, False),
        (408, True),
        (409, True),
        (425, True),
        (429, True),
        (500, True),
        (503, True),
    ],
)
def test_http_errors_preserve_status_and_retryability(
    status_code: int,
    expected_retryable: bool,
) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code,
            json={"error": {"message": "capacity exhausted", "code": "overloaded"}},
        )

    provider = OpenAICompatibleProvider(
        base_url="http://localhost:8000/v1",
        model="local-model",
        transport_retries=0,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ProviderError) as raised:
        provider.complete([_user_message()])
    provider.close()

    assert raised.value.code is ProviderErrorCode.HTTP_ERROR
    assert raised.value.message == "capacity exhausted"
    assert raised.value.status_code == status_code
    assert raised.value.retryable is expected_retryable
    assert raised.value.details == {"server_code": "overloaded"}


def test_non_json_http_error_uses_stable_fallback_details() -> None:
    provider = OpenAICompatibleProvider(
        base_url="http://localhost:8000/v1",
        model="local-model",
        transport_retries=0,
        transport=httpx.MockTransport(lambda _: httpx.Response(502, text="proxy secret")),
    )
    with pytest.raises(ProviderError) as raised:
        provider.complete([_user_message()])
    provider.close()

    assert raised.value.message == "model server returned HTTP 502"
    assert raised.value.details == {"server_code": "http_502"}
    assert "secret" not in raised.value.message


def test_timeout_is_exposed_as_retryable_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow model", request=request)

    provider = OpenAICompatibleProvider(
        base_url="http://localhost:8000/v1",
        model="local-model",
        transport_retries=0,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ProviderError) as raised:
        provider.complete([_user_message()])
    provider.close()

    assert raised.value.code is ProviderErrorCode.TIMEOUT
    assert raised.value.retryable is True


def test_transport_failure_is_exposed_without_leaking_exception_text() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("secret endpoint detail", request=request)

    provider = OpenAICompatibleProvider(
        base_url="http://localhost:8000/v1",
        model="local-model",
        transport_retries=0,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ProviderError) as raised:
        provider.complete([_user_message()])
    provider.close()

    assert raised.value.code is ProviderErrorCode.TRANSPORT_ERROR
    assert raised.value.details == {"exception_type": "ConnectError"}
    assert "secret" not in raised.value.message


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"not-json"),
        httpx.Response(200, json={"choices": []}),
        httpx.Response(
            200,
            json={"choices": [{"index": 0, "message": {"role": "user", "content": "wrong role"}}]},
        ),
        httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "duplicate",
                                    "type": "function",
                                    "function": {"name": "first", "arguments": "{}"},
                                },
                                {
                                    "id": "duplicate",
                                    "type": "function",
                                    "function": {"name": "second", "arguments": "{}"},
                                },
                            ],
                        },
                    }
                ]
            },
        ),
        httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "done"},
                    }
                ],
                "usage": {
                    "prompt_tokens": 4,
                    "completion_tokens": 2,
                    "total_tokens": 5,
                },
            },
        ),
        httpx.Response(
            200,
            json={
                "model": "unexpected-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "done"},
                    }
                ],
            },
        ),
    ],
)
def test_invalid_completion_is_a_typed_non_retryable_error(response: httpx.Response) -> None:
    provider = OpenAICompatibleProvider(
        base_url="http://localhost:8000/v1",
        model="local-model",
        transport=httpx.MockTransport(lambda _: response),
    )
    with pytest.raises(ProviderError) as raised:
        provider.complete([_user_message()])
    provider.close()

    assert raised.value.code is ProviderErrorCode.INVALID_RESPONSE
    assert raised.value.retryable is False
    assert raised.value.provider_attempts == 1
    assert raised.value.retry_error_codes == ()
    assert raised.value.attempt_error_codes == ("invalid_response",)
    assert raised.value.latency_ms is not None and raised.value.latency_ms >= 0


def test_retryable_failures_repeat_identical_full_posts_with_bounded_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request_bodies: list[bytes] = []
    delays: list[float] = []
    clock_values = iter((10.0, 10.75))
    monkeypatch.setattr(provider_module, "perf_counter", lambda: next(clock_values))

    def handler(request: httpx.Request) -> httpx.Response:
        request_bodies.append(request.content)
        attempt = len(request_bodies)
        if attempt == 1:
            raise httpx.ReadTimeout("slow", request=request)
        if attempt == 2:
            raise httpx.ConnectError("offline", request=request)
        if attempt == 3:
            return httpx.Response(429, json={"error": {"code": "busy"}})
        return httpx.Response(
            200,
            json={
                "model": "local-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "recovered"},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    provider = OpenAICompatibleProvider(
        base_url="http://localhost:8000/v1",
        model="local-model",
        transport_retries=3,
        backoff_seconds=0.125,
        sleeper=delays.append,
        transport=httpx.MockTransport(handler),
    )
    result = provider.complete([_user_message()])
    provider.close()

    assert result.message.content == "recovered"
    assert result.provider_attempts == 4
    assert result.retry_error_codes == ("timeout", "transport_error", "http_error")
    assert result.latency_ms == pytest.approx(750.0)
    assert delays == [0.125, 0.125, 0.125]
    assert len(request_bodies) == 4
    assert len(set(request_bodies)) == 1


def test_retry_budget_is_bounded_and_recorded_on_terminal_error() -> None:
    calls = 0
    delays: list[float] = []

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503, json={"error": {"code": "overloaded"}})

    provider = OpenAICompatibleProvider(
        base_url="http://localhost:8000/v1",
        model="local-model",
        transport_retries=2,
        backoff_seconds=0.0,
        sleeper=delays.append,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ProviderError) as raised:
        provider.complete([_user_message()])
    provider.close()

    assert calls == 3
    assert delays == [0.0, 0.0]
    assert raised.value.code is ProviderErrorCode.HTTP_ERROR
    assert raised.value.details["server_code"] == "overloaded"
    assert raised.value.provider_attempts == 3
    assert raised.value.retry_error_codes == ("http_error", "http_error")
    assert raised.value.attempt_error_codes == ("http_error", "http_error", "http_error")
    assert raised.value.latency_ms is not None and raised.value.latency_ms >= 0


def test_invalid_response_preserves_prior_retry_codes_and_terminal_code() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, json={"error": {"code": "overloaded"}})
        return httpx.Response(200, content=b"not-json")

    provider = OpenAICompatibleProvider(
        base_url="http://localhost:8000/v1",
        model="local-model",
        transport_retries=1,
        backoff_seconds=0.0,
        sleeper=lambda _: None,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ProviderError) as raised:
        provider.complete([_user_message()])
    provider.close()

    assert raised.value.code is ProviderErrorCode.INVALID_RESPONSE
    assert raised.value.provider_attempts == 2
    assert raised.value.retry_error_codes == ("http_error",)
    assert raised.value.attempt_error_codes == ("http_error", "invalid_response")
    assert raised.value.latency_ms is not None and raised.value.latency_ms >= 0


def test_non_retryable_http_error_ignores_available_retry_budget() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(422, json={"error": {"code": "invalid_request"}})

    provider = OpenAICompatibleProvider(
        base_url="http://localhost:8000/v1",
        model="local-model",
        transport_retries=5,
        sleeper=lambda _: pytest.fail("non-retryable failures must not sleep"),
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ProviderError) as raised:
        provider.complete([_user_message()])
    provider.close()

    assert calls == 1
    assert raised.value.retryable is False


def test_model_preflight_lists_models_and_accepts_exact_configured_id() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [
                    {
                        "id": "Qwen/Qwen3-8B",
                        "object": "model",
                        "created": 123,
                        "owned_by": "local",
                    },
                    {"id": "embedding-model", "object": "model"},
                ],
                "extra_server_field": True,
            },
        )

    provider = OpenAICompatibleProvider(
        base_url="http://localhost:8000/v1/",
        model="Qwen/Qwen3-8B",
        api_key="local-secret",
        transport=httpx.MockTransport(handler),
    )
    assert provider.available_models() == ("Qwen/Qwen3-8B", "embedding-model")
    observed_models = provider.check_model_available()
    provider.close()

    assert observed_models == ("Qwen/Qwen3-8B", "embedding-model")
    assert len(requests) == 2
    assert all(request.method == "GET" for request in requests)
    assert all(request.url == "http://localhost:8000/v1/models" for request in requests)
    assert all(request.headers["Authorization"] == "Bearer local-secret" for request in requests)


def test_model_preflight_rejects_missing_configured_model() -> None:
    provider = OpenAICompatibleProvider(
        base_url="http://localhost:8000/v1",
        model="missing-model",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "object": "list",
                    "data": [{"id": "loaded-model", "object": "model"}],
                },
            )
        ),
    )
    with pytest.raises(ProviderError, match="missing-model") as raised:
        provider.check_model_available()
    provider.close()

    assert raised.value.code is ProviderErrorCode.INVALID_REQUEST
    assert raised.value.details == {
        "configured_model": "missing-model",
        "available_models": ["loaded-model"],
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"object": "collection", "data": []},
        {"object": "list", "data": [{"id": "", "object": "model"}]},
        {
            "object": "list",
            "data": [
                {"id": "duplicate", "object": "model"},
                {"id": "duplicate", "object": "model"},
            ],
        },
    ],
)
def test_model_preflight_rejects_malformed_list_payload(payload: object) -> None:
    provider = OpenAICompatibleProvider(
        base_url="http://localhost:8000/v1",
        model="local-model",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload)),
    )
    with pytest.raises(ProviderError) as raised:
        provider.available_models()
    provider.close()

    assert raised.value.code is ProviderErrorCode.INVALID_RESPONSE
    assert raised.value.retryable is False


@pytest.mark.parametrize("failure", ["timeout", "transport", "http"])
def test_model_preflight_exposes_typed_server_failures(failure: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if failure == "timeout":
            raise httpx.ReadTimeout("slow", request=request)
        if failure == "transport":
            raise httpx.ConnectError("offline", request=request)
        return httpx.Response(503, json={"error": {"code": "offline"}})

    provider = OpenAICompatibleProvider(
        base_url="http://localhost:8000/v1",
        model="local-model",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ProviderError) as raised:
        provider.available_models()
    provider.close()

    expected = {
        "timeout": ProviderErrorCode.TIMEOUT,
        "transport": ProviderErrorCode.TRANSPORT_ERROR,
        "http": ProviderErrorCode.HTTP_ERROR,
    }
    assert raised.value.code is expected[failure]


@pytest.mark.parametrize("operation", ["completion", "preflight"])
@pytest.mark.parametrize("failure", ["invalid_url", "unicode"])
def test_httpx_invalid_url_is_normalized_without_retry_or_raw_message(
    operation: str,
    failure: str,
) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        if failure == "invalid_url":
            raise httpx.InvalidURL("secret malformed URL detail")
        raise UnicodeError("secret IDNA detail")

    provider = OpenAICompatibleProvider(
        base_url="http://localhost:8000/v1",
        model="local-model",
        transport_retries=5,
        sleeper=lambda _: pytest.fail("invalid URLs must not be retried"),
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ProviderError) as raised:
        if operation == "completion":
            provider.complete([_user_message()])
        else:
            provider.available_models()
    provider.close()

    assert raised.value.code is ProviderErrorCode.INVALID_REQUEST
    assert raised.value.retryable is False
    assert raised.value.provider_attempts == 1
    assert raised.value.retry_error_codes == ()
    assert raised.value.attempt_error_codes == ("invalid_request",)
    if operation == "completion":
        assert raised.value.latency_ms is not None and raised.value.latency_ms >= 0
    assert "secret" not in raised.value.message


def test_empty_messages_and_reserved_extra_fields_fail_before_network() -> None:
    provider = OpenAICompatibleProvider(
        base_url="http://localhost:8000/v1",
        model="local-model",
        transport=httpx.MockTransport(lambda _: pytest.fail("network must not be called")),
    )
    with pytest.raises(ProviderError) as raised:
        provider.complete([])
    with pytest.raises(ProviderError, match="reserved fields") as conflict:
        provider.complete([_user_message()], extra_body={"model": "different-model"})
    provider.close()

    assert raised.value.code is ProviderErrorCode.INVALID_REQUEST
    assert raised.value.retryable is False
    assert raised.value.provider_attempts == 1
    assert raised.value.retry_error_codes == ()
    assert raised.value.attempt_error_codes == ("invalid_request",)
    assert raised.value.latency_ms is not None and raised.value.latency_ms >= 0
    assert conflict.value.code is ProviderErrorCode.INVALID_REQUEST


def test_public_message_and_usage_models_reject_inconsistent_fields() -> None:
    tool_call = ToolCall(
        id="call-1",
        function=ToolCallFunction(name="get_metrics", arguments="{}"),
    )
    with pytest.raises(ValidationError, match="tool messages require"):
        ChatMessage(role=ChatRole.TOOL, content="{}")
    with pytest.raises(ValidationError, match="only valid on tool"):
        ChatMessage(role=ChatRole.USER, content="hello", tool_call_id="call-1")
    with pytest.raises(ValidationError, match="only valid on assistant"):
        ChatMessage(role=ChatRole.USER, content="hello", tool_calls=(tool_call,))
    with pytest.raises(ValidationError, match="total_tokens"):
        TokenUsage(prompt_tokens=4, completion_tokens=2, total_tokens=5)
    with pytest.raises(ValidationError, match="retry_error_codes"):
        ChatCompletion(
            id="chatcmpl-invalid",
            model="local-model",
            message=ChatMessage(role=ChatRole.ASSISTANT, content="done"),
            finish_reason="stop",
            latency_ms=1.0,
            provider_attempts=1,
            retry_error_codes=("timeout",),
        )
    with pytest.raises(ValueError, match="provider_attempts"):
        ProviderError(
            ProviderErrorCode.TIMEOUT,
            "invalid metadata",
            retryable=True,
            provider_attempts=0,
        )
    with pytest.raises(ValueError, match="latency_ms"):
        ProviderError(
            ProviderErrorCode.TIMEOUT,
            "invalid latency",
            retryable=True,
            latency_ms=float("nan"),
        )


def test_default_transport_disables_hidden_httpx_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_retries: list[int] = []

    class RecordingTransport(httpx.BaseTransport):
        def __init__(self, *, retries: int) -> None:
            observed_retries.append(retries)

        def handle_request(self, _: httpx.Request) -> httpx.Response:
            pytest.fail("network must not be called")

    monkeypatch.setattr(httpx, "HTTPTransport", RecordingTransport)
    provider = OpenAICompatibleProvider(
        base_url="http://localhost:8000/v1",
        model="local-model",
        transport_retries=7,
    )
    provider.close()

    assert observed_retries == [0]


def test_http_client_ignores_environment_proxy_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client_options: dict[str, object] = {}

    class RecordingClient:
        def __init__(self, **kwargs: object) -> None:
            client_options.update(kwargs)

        def close(self) -> None:
            pass

    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:9999")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:9999")
    monkeypatch.setattr(httpx, "Client", RecordingClient)
    provider = OpenAICompatibleProvider(
        base_url="http://127.0.0.1:8000/v1",
        model="local-model",
        transport=httpx.MockTransport(lambda _: pytest.fail("network must not be called")),
    )
    provider.close()

    assert client_options["trust_env"] is False


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"base_url": "", "model": "m"}, "base_url"),
        ({"base_url": "http://[invalid/v1", "model": "m"}, "base_url"),
        ({"base_url": "http://local host/v1", "model": "m"}, "base_url"),
        ({"base_url": "http://local\x00host/v1", "model": "m"}, "base_url"),
        ({"base_url": "http://a..b/v1", "model": "m"}, "base_url"),
        ({"base_url": "http://\ud800/v1", "model": "m"}, "base_url"),
        ({"base_url": "not a url", "model": "m"}, "base_url"),
        ({"base_url": "http://local/v1", "model": ""}, "model"),
        ({"base_url": "http://local/v1", "model": "m", "timeout": 0.0}, "timeout"),
        ({"base_url": "http://local/v1", "model": "m", "timeout": float("nan")}, "timeout"),
        (
            {"base_url": "http://local/v1", "model": "m", "transport_retries": -1},
            "transport_retries",
        ),
        (
            {"base_url": "http://local/v1", "model": "m", "backoff_seconds": -0.1},
            "backoff_seconds",
        ),
        (
            {"base_url": "http://local/v1", "model": "m", "backoff_seconds": float("nan")},
            "backoff_seconds",
        ),
        (
            {"base_url": "http://local/v1", "model": "m", "temperature": -0.1},
            "temperature",
        ),
        (
            {"base_url": "http://local/v1", "model": "m", "temperature": float("inf")},
            "temperature",
        ),
        (
            {"base_url": "http://local/v1", "model": "m", "extra_body": {"seed": 2}},
            "reserved fields",
        ),
    ],
)
def test_invalid_configuration_is_rejected(kwargs: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        OpenAICompatibleProvider(**kwargs)  # type: ignore[arg-type]
