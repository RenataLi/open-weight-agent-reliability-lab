"""Provider protocol and stable failure boundary."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import StrEnum
from math import isfinite
from typing import Protocol

from pydantic import JsonValue

from agent_reliability_lab.providers.models import ChatCompletion, ChatMessage, ChatTool


class ProviderErrorCode(StrEnum):
    """Machine-readable provider failure classes used by runners and reports."""

    INVALID_REQUEST = "invalid_request"
    TIMEOUT = "timeout"
    TRANSPORT_ERROR = "transport_error"
    HTTP_ERROR = "http_error"
    INVALID_RESPONSE = "invalid_response"


class ProviderError(RuntimeError):
    """Expected inference failure with retry and HTTP metadata."""

    def __init__(
        self,
        code: ProviderErrorCode,
        message: str,
        *,
        retryable: bool,
        status_code: int | None = None,
        details: Mapping[str, JsonValue] | None = None,
        provider_attempts: int = 1,
        retry_error_codes: Sequence[str] = (),
        attempt_error_codes: Sequence[str] | None = None,
        latency_ms: float | None = None,
    ) -> None:
        if provider_attempts < 1:
            raise ValueError("provider_attempts must be at least one")
        retry_codes = tuple(retry_error_codes)
        attempt_codes = (code.value,) if attempt_error_codes is None else tuple(attempt_error_codes)
        if len(retry_codes) != provider_attempts - 1:
            raise ValueError("retry_error_codes must describe attempts before the terminal one")
        if len(attempt_codes) != provider_attempts:
            raise ValueError("attempt_error_codes must describe every failed attempt")
        if latency_ms is not None and (not isfinite(latency_ms) or latency_ms < 0):
            raise ValueError("latency_ms must be finite and non-negative")
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.status_code = status_code
        self.details = dict(details or {})
        self.provider_attempts = provider_attempts
        self.retry_error_codes = retry_codes
        self.attempt_error_codes = attempt_codes
        self.latency_ms = latency_ms


class ChatProvider(Protocol):
    """Synchronous inference interface consumed by benchmark runners."""

    @property
    def model(self) -> str:
        """Configured request model identifier."""
        ...

    def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        tools: Sequence[ChatTool] | None = None,
        extra_body: Mapping[str, JsonValue] | None = None,
    ) -> ChatCompletion:
        """Return the first assistant choice or raise ``ProviderError``."""
        ...

    def close(self) -> None:
        """Release provider-owned resources."""
        ...
