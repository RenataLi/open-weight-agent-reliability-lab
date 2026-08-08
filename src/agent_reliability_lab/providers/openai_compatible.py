"""Synchronous client for local OpenAI-compatible chat-completion servers."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from math import isfinite
from time import perf_counter, sleep
from typing import Annotated, Any, Literal, Self
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from agent_reliability_lab.providers.base import ProviderError, ProviderErrorCode
from agent_reliability_lab.providers.models import (
    ChatCompletion,
    ChatMessage,
    ChatRole,
    ChatTool,
    TokenUsage,
    ToolCall,
)

_RESERVED_BODY_FIELDS = frozenset({"messages", "model", "seed", "temperature", "tools"})
_RETRYABLE_STATUS_CODES = frozenset({408, 409, 425, 429})
_NonNegativeInt = Annotated[int, Field(strict=True, ge=0)]


class _WireModel(BaseModel):
    """Tolerant wire model: compatible servers may add response fields."""

    model_config = ConfigDict(extra="ignore")


class _WireUsage(_WireModel):
    prompt_tokens: _NonNegativeInt
    completion_tokens: _NonNegativeInt
    total_tokens: _NonNegativeInt


class _WireMessage(_WireModel):
    role: Literal["assistant"]
    content: str | None = None
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()


class _WireChoice(_WireModel):
    index: _NonNegativeInt
    message: _WireMessage
    finish_reason: str | None = None


class _WireCompletion(_WireModel):
    id: str = ""
    model: str | None = None
    choices: tuple[_WireChoice, ...]
    usage: _WireUsage | None = None


class _WireModelCard(_WireModel):
    id: str = Field(min_length=1)
    object: Literal["model"]


class _WireModelList(_WireModel):
    object: Literal["list"]
    data: tuple[_WireModelCard, ...]


class OpenAICompatibleProvider:
    """OpenAI chat-completions subset with deterministic request defaults."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None = None,
        timeout: float = 120.0,
        transport_retries: int = 2,
        seed: int | None = 0,
        temperature: float = 0.0,
        tools: Sequence[ChatTool] = (),
        extra_body: Mapping[str, JsonValue] | None = None,
        transport: httpx.BaseTransport | None = None,
        sleeper: Callable[[float], None] | None = None,
        backoff_seconds: float = 0.25,
    ) -> None:
        normalized_url = self._normalize_base_url(base_url)
        if not model.strip():
            raise ValueError("model must not be empty")
        if not isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be positive")
        if transport_retries < 0:
            raise ValueError("transport_retries must be non-negative")
        if not isfinite(backoff_seconds) or backoff_seconds < 0:
            raise ValueError("backoff_seconds must be non-negative")
        if not isfinite(temperature) or temperature < 0:
            raise ValueError("temperature must be non-negative")

        self._endpoint = f"{normalized_url}/chat/completions"
        self._models_endpoint = f"{normalized_url}/models"
        self._model = model
        self._transport_retries = transport_retries
        self._sleeper = sleeper or sleep
        self._backoff_seconds = backoff_seconds
        self._seed = seed
        self._temperature = temperature
        self._tools = tuple(tools)
        self._extra_body = dict(extra_body or {})
        self._validate_extra_body(self._extra_body)

        headers = {"Accept": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        http_transport = transport if transport is not None else httpx.HTTPTransport(retries=0)
        self._client = httpx.Client(
            headers=headers,
            timeout=httpx.Timeout(timeout),
            transport=http_transport,
            trust_env=False,
        )

    @property
    def model(self) -> str:
        return self._model

    def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        tools: Sequence[ChatTool] | None = None,
        extra_body: Mapping[str, JsonValue] | None = None,
    ) -> ChatCompletion:
        started = perf_counter()
        if not messages:
            raise ProviderError(
                ProviderErrorCode.INVALID_REQUEST,
                "at least one chat message is required",
                retryable=False,
                latency_ms=self._elapsed_ms(started),
            )

        try:
            request_body = self._request_body(messages, tools=tools, extra_body=extra_body)
        except ValueError as exc:
            raise ProviderError(
                ProviderErrorCode.INVALID_REQUEST,
                str(exc),
                retryable=False,
                latency_ms=self._elapsed_ms(started),
            ) from exc
        try:
            response, provider_attempts, retry_error_codes = self._post_with_retries(request_body)
        except ProviderError as exc:
            exc.latency_ms = self._elapsed_ms(started)
            raise
        latency_ms = self._elapsed_ms(started)
        try:
            wire = _WireCompletion.model_validate(response.json())
            if wire.model is not None and wire.model != self._model:
                raise ValueError("response model does not match the configured request model")
            choice = wire.choices[0]
            tool_call_ids = [tool_call.id for tool_call in choice.message.tool_calls]
            if len(tool_call_ids) != len(set(tool_call_ids)):
                raise ValueError("assistant response contains duplicate tool-call IDs")
            message = ChatMessage(
                role=ChatRole.ASSISTANT,
                content=choice.message.content,
                name=choice.message.name,
                tool_call_id=choice.message.tool_call_id,
                tool_calls=choice.message.tool_calls,
            )
            usage = (
                TokenUsage(
                    prompt_tokens=wire.usage.prompt_tokens,
                    completion_tokens=wire.usage.completion_tokens,
                    total_tokens=wire.usage.total_tokens,
                )
                if wire.usage is not None
                else None
            )
        except (IndexError, ValueError, ValidationError) as exc:
            raise ProviderError(
                ProviderErrorCode.INVALID_RESPONSE,
                "server returned an invalid chat-completion response",
                retryable=False,
                details={"exception_type": type(exc).__name__},
                provider_attempts=provider_attempts,
                retry_error_codes=retry_error_codes,
                attempt_error_codes=(
                    *retry_error_codes,
                    ProviderErrorCode.INVALID_RESPONSE.value,
                ),
                latency_ms=self._elapsed_ms(started),
            ) from exc

        return ChatCompletion(
            id=wire.id,
            model=wire.model or self._model,
            message=message,
            finish_reason=choice.finish_reason,
            usage=usage,
            latency_ms=latency_ms,
            provider_attempts=provider_attempts,
            retry_error_codes=retry_error_codes,
        )

    def available_models(self) -> tuple[str, ...]:
        response = self._get_models()
        try:
            wire = _WireModelList.model_validate(response.json())
            model_ids = tuple(card.id for card in wire.data)
            if len(model_ids) != len(set(model_ids)):
                raise ValueError("model list contains duplicate IDs")
        except (ValueError, ValidationError) as exc:
            raise ProviderError(
                ProviderErrorCode.INVALID_RESPONSE,
                "server returned an invalid model-list response",
                retryable=False,
                details={"exception_type": type(exc).__name__},
            ) from exc
        return model_ids

    def check_model_available(self) -> tuple[str, ...]:
        available_models = self.available_models()
        if self._model not in available_models:
            raise ProviderError(
                ProviderErrorCode.INVALID_REQUEST,
                f"configured model is not available: {self._model}",
                retryable=False,
                details={
                    "configured_model": self._model,
                    "available_models": list(available_models),
                },
            )
        return available_models

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _post_with_retries(
        self,
        request_body: dict[str, Any],
    ) -> tuple[httpx.Response, int, tuple[str, ...]]:
        retry_error_codes: list[str] = []
        for attempt_index in range(self._transport_retries + 1):
            try:
                response = self._post_once(request_body)
            except ProviderError as exc:
                has_retry_remaining = attempt_index < self._transport_retries
                if not exc.retryable or not has_retry_remaining:
                    exc.provider_attempts = attempt_index + 1
                    exc.retry_error_codes = tuple(retry_error_codes)
                    exc.attempt_error_codes = (*retry_error_codes, exc.code.value)
                    raise
                retry_error_codes.append(exc.code.value)
                self._sleeper(self._backoff_seconds)
                continue
            return response, attempt_index + 1, tuple(retry_error_codes)
        raise AssertionError("bounded provider retry loop did not terminate")

    def _post_once(self, request_body: dict[str, Any]) -> httpx.Response:
        try:
            response = self._client.post(self._endpoint, json=request_body)
        except (httpx.InvalidURL, UnicodeError) as exc:
            raise ProviderError(
                ProviderErrorCode.INVALID_REQUEST,
                "model server URL is invalid",
                retryable=False,
                details={"exception_type": type(exc).__name__},
            ) from exc
        except httpx.TimeoutException as exc:
            raise ProviderError(
                ProviderErrorCode.TIMEOUT,
                "model request timed out",
                retryable=True,
            ) from exc
        except httpx.TransportError as exc:
            raise ProviderError(
                ProviderErrorCode.TRANSPORT_ERROR,
                "model transport failed",
                retryable=True,
                details={"exception_type": type(exc).__name__},
            ) from exc
        if not response.is_success:
            raise self._http_error(response)
        return response

    def _get_models(self) -> httpx.Response:
        try:
            response = self._client.get(self._models_endpoint)
        except (httpx.InvalidURL, UnicodeError) as exc:
            raise ProviderError(
                ProviderErrorCode.INVALID_REQUEST,
                "model server URL is invalid",
                retryable=False,
                details={"exception_type": type(exc).__name__},
            ) from exc
        except httpx.TimeoutException as exc:
            raise ProviderError(
                ProviderErrorCode.TIMEOUT,
                "model-list request timed out",
                retryable=True,
            ) from exc
        except httpx.TransportError as exc:
            raise ProviderError(
                ProviderErrorCode.TRANSPORT_ERROR,
                "model-list transport failed",
                retryable=True,
                details={"exception_type": type(exc).__name__},
            ) from exc
        if not response.is_success:
            raise self._http_error(response)
        return response

    @staticmethod
    def _normalize_base_url(base_url: str) -> str:
        if not base_url or any(
            character.isspace() or ord(character) < 32 or ord(character) == 127
            for character in base_url
        ):
            raise ValueError("base_url must not contain whitespace or control characters")
        try:
            parsed = urlsplit(base_url)
            _ = parsed.port
        except (UnicodeError, ValueError) as exc:
            raise ValueError("base_url is not a valid URL") from exc
        if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
            raise ValueError("base_url must be an absolute HTTP(S) URL")
        hostname = parsed.hostname
        try:
            hostname.encode("idna")
        except UnicodeError as exc:
            raise ValueError("base_url hostname is not valid IDNA") from exc
        if any(not label for label in hostname.rstrip(".").split(".")):
            raise ValueError("base_url hostname contains an empty label")
        if parsed.query or parsed.fragment:
            raise ValueError("base_url must not contain a query or fragment")
        return base_url.rstrip("/")

    @staticmethod
    def _elapsed_ms(started: float) -> float:
        return max(0.0, (perf_counter() - started) * 1000.0)

    def _request_body(
        self,
        messages: Sequence[ChatMessage],
        *,
        tools: Sequence[ChatTool] | None,
        extra_body: Mapping[str, JsonValue] | None,
    ) -> dict[str, Any]:
        selected_tools = self._tools if tools is None else tuple(tools)
        body: dict[str, Any] = {
            "model": self._model,
            "messages": [self._serialize_message(message) for message in messages],
            "temperature": self._temperature,
        }
        if self._seed is not None:
            body["seed"] = self._seed
        if selected_tools:
            body["tools"] = [tool.model_dump(mode="json") for tool in selected_tools]

        merged_extra = dict(self._extra_body)
        if extra_body:
            self._validate_extra_body(extra_body)
            merged_extra.update(extra_body)
        body.update(merged_extra)
        return body

    @staticmethod
    def _serialize_message(message: ChatMessage) -> dict[str, Any]:
        payload = message.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
        if message.tool_calls:
            # OpenAI-compatible servers require the discriminator even though it is
            # the provider-neutral model's default value.
            payload["tool_calls"] = [
                tool_call.model_dump(mode="json") for tool_call in message.tool_calls
            ]
        return payload

    @staticmethod
    def _validate_extra_body(extra_body: Mapping[str, JsonValue]) -> None:
        conflicts = sorted(_RESERVED_BODY_FIELDS.intersection(extra_body))
        if conflicts:
            joined = ", ".join(conflicts)
            raise ValueError(f"extra_body cannot override reserved fields: {joined}")

    @staticmethod
    def _http_error(response: httpx.Response) -> ProviderError:
        status_code = response.status_code
        message = f"model server returned HTTP {status_code}"
        server_code: JsonValue = f"http_{status_code}"
        try:
            payload: object = response.json()
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                raw_message = error.get("message")
                raw_code = error.get("code")
                if isinstance(raw_message, str) and raw_message:
                    message = raw_message
                if isinstance(raw_code, (str, int)):
                    server_code = raw_code
        retryable = status_code in _RETRYABLE_STATUS_CODES or status_code >= 500
        return ProviderError(
            ProviderErrorCode.HTTP_ERROR,
            message,
            retryable=retryable,
            status_code=status_code,
            details={"server_code": server_code},
        )
