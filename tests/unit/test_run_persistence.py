from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_reliability_lab.agent import persistence
from agent_reliability_lab.agent.persistence import RunStore, write_json_atomic


def test_run_store_never_overwrites_and_persists_jsonl(tmp_path: Path) -> None:
    store = RunStore(tmp_path, "run-0001")
    store.start({"model": "local-model", "api_key_present": False})
    store.append({"kind": "model_turn", "turn": 1})
    store.append({"kind": "tool_result", "status": "ok"})

    events = [
        json.loads(line)
        for line in (store.run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert events == [
        {"kind": "model_turn", "turn": 1},
        {"kind": "tool_result", "status": "ok"},
    ]
    with pytest.raises(FileExistsError):
        RunStore(tmp_path, "run-0001").start({"model": "other"})


def test_atomic_json_has_no_temporary_file_after_success(tmp_path: Path) -> None:
    destination = tmp_path / "nested" / "result.json"
    write_json_atomic(destination, {"passed": True})

    assert json.loads(destination.read_text(encoding="utf-8")) == {"passed": True}
    assert not destination.with_name(".result.json.tmp").exists()


def test_run_store_start_is_transactional_on_manifest_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_write(_path: Path, _value: object) -> None:
        raise OSError("simulated manifest failure")

    monkeypatch.setattr(persistence, "write_json_atomic", fail_write)
    store = RunStore(tmp_path, "transactional-run")

    with pytest.raises(OSError, match="simulated manifest failure"):
        store.start({"model": "local-model"})

    assert not store.run_dir.exists()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "run_id",
    [
        "",
        ".",
        "..",
        "../escape",
        "contains space",
        "a/b",
        "name.",
        "CON",
        "con.txt",
        "LPT1.json",
    ],
)
def test_run_id_is_path_safe(tmp_path: Path, run_id: str) -> None:
    with pytest.raises(ValueError):
        RunStore(tmp_path, run_id)
