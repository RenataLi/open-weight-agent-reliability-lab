"""Crash-tolerant local artifacts for model benchmark runs."""

from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Any

from pydantic import BaseModel, JsonValue

_SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_WINDOWS_RESERVED_STEMS = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{index}" for index in range(1, 10)}
    | {f"LPT{index}" for index in range(1, 10)}
)


def _payload(value: BaseModel | dict[str, JsonValue]) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return value


def write_json_atomic(path: Path, value: BaseModel | dict[str, JsonValue]) -> None:
    """Write complete UTF-8 JSON without exposing a partially written result."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    text = json.dumps(
        _payload(value),
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
        allow_nan=False,
    )
    temporary.write_text(f"{text}\n", encoding="utf-8", newline="\n")
    os.replace(temporary, path)


class RunStore:
    """One append-only event stream plus atomic manifest and result files."""

    def __init__(self, root: Path, run_id: str) -> None:
        reserved_stem = run_id.split(".", maxsplit=1)[0].upper()
        if (
            not _SAFE_RUN_ID.fullmatch(run_id)
            or run_id in {".", ".."}
            or run_id.endswith(".")
            or reserved_stem in _WINDOWS_RESERVED_STEMS
        ):
            raise ValueError(
                "run_id must be a portable filename using letters, digits, dot, dash, or underscore"
            )
        self.run_dir = root / run_id
        self._events_path = self.run_dir / "events.jsonl"
        self._started = False

    def start(self, manifest: dict[str, JsonValue]) -> None:
        if self.run_dir.exists():
            raise FileExistsError(f"run directory already exists: {self.run_dir}")
        self.run_dir.parent.mkdir(parents=True, exist_ok=True)
        staging = self.run_dir.parent / f".{self.run_dir.name}.starting-{uuid.uuid4().hex}"
        staging.mkdir()
        try:
            write_json_atomic(staging / "manifest.json", manifest)
            (staging / "events.jsonl").touch(exist_ok=False)
            os.replace(staging, self.run_dir)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        self._started = True

    def append(self, event: dict[str, JsonValue]) -> None:
        if not self._started:
            raise RuntimeError("run store has not been started")
        line = json.dumps(event, ensure_ascii=False, sort_keys=True, allow_nan=False)
        with self._events_path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(f"{line}\n")
            stream.flush()
            os.fsync(stream.fileno())

    def finish(self, result: BaseModel) -> Path:
        if not self._started:
            raise RuntimeError("run store has not been started")
        result_path = self.run_dir / "result.json"
        write_json_atomic(result_path, result)
        return result_path
