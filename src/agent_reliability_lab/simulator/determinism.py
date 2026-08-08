"""Canonical serialization, stable hashes, and deterministic identifiers."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from enum import Enum
from typing import cast

from pydantic import BaseModel, JsonValue

type JsonObject = dict[str, JsonValue]


def _to_json_value(value: object) -> JsonValue:
    if isinstance(value, BaseModel):
        return _to_json_value(value.model_dump(mode="json", exclude_none=True))
    if isinstance(value, Enum):
        return _to_json_value(value.value)
    if isinstance(value, Mapping):
        mapping = cast(Mapping[object, object], value)
        return {str(key): _to_json_value(item) for key, item in mapping.items()}
    if isinstance(value, tuple | list):
        return [_to_json_value(item) for item in value]
    if isinstance(value, set | frozenset):
        return sorted((_to_json_value(item) for item in value), key=canonical_json)
    if value is None or isinstance(value, bool | int | float | str):
        return value
    raise TypeError(f"Unsupported canonical value: {type(value).__name__}")


def canonical_json(value: object) -> str:
    """Serialize a value without platform- or insertion-order-dependent output."""

    return json.dumps(
        _to_json_value(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def stable_digest(value: object) -> str:
    """Return a SHA-256 digest of the canonical representation."""

    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def stable_id(prefix: str, sequence: int) -> str:
    """Create a human-readable deterministic identifier."""

    if sequence < 1:
        raise ValueError("sequence must be positive")
    return f"{prefix}-{sequence:04d}"


def trace_digest(items: Sequence[object]) -> str:
    """Hash an ordered trace."""

    return stable_digest(list(items))
