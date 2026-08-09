from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_reliability_lab.study import (
    StudySpec,
    build_study_plan,
    canonical_sha256,
    load_study_spec,
    verify_study_inputs,
)
from agent_reliability_lab.study.models import StudyPhase

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROTOCOL_PATH = PROJECT_ROOT / "studies" / "m3_protocol.json"


def test_preregistered_plan_has_stable_complete_cartesian_matrix() -> None:
    spec = load_study_spec(PROTOCOL_PATH)
    first = build_study_plan(spec)
    second = build_study_plan(spec)

    assert first == second
    assert canonical_sha256(first) == canonical_sha256(second)
    assert canonical_sha256(first) == (
        "9bfff599cd0444ae61b7a28681182bef239e92b84a10600c5e0f7f3c6128b065"
    )
    assert len(first.cells) == 24
    assert len({cell.cell_id for cell in first.cells}) == 24
    assert sum(cell.phase is StudyPhase.STOCHASTIC for cell in first.cells) == 20
    assert sum(cell.phase is StudyPhase.GREEDY_ANCHOR for cell in first.cells) == 4
    assert first.stochastic_task_runs == 160
    assert first.greedy_anchor_task_runs == 32
    assert first.total_task_runs == 192
    assert first.cells[0].cell_id == "m3s-qwen35-9b-v1-s00000011-r01"
    assert first.cells[1].cell_id == "m3s-qwen35-9b-v2-s00000011-r01"
    assert first.cells[2].cell_id == "m3s-qwen35-9b-v1-s00000023-r01"
    assert first.cells[-1].cell_id == "m3g-ministral3-8b-v2-s00000000-r01"


def test_protocol_hash_and_every_pinned_input_are_reproducible() -> None:
    spec = load_study_spec(PROTOCOL_PATH)

    verify_study_inputs(spec, PROJECT_ROOT)

    assert canonical_sha256(spec) == (
        "e3d28dea41f2a3d40c94ebbc363559d1a188b1f1662caed1bb49b15dac962d84"
    )
    assert spec.dataset.fixture_worlds == 3
    assert [model.revision for model in spec.models] == [
        "cc5442c03a5c0bff0bd4c6888d9a40029c637733",
        "f6fae9795746f63c9be8344932f01275f3c63734",
    ]


def test_protocol_rejects_posthoc_gate_relaxation() -> None:
    raw = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
    raw["gates"]["min_model_pass_rate"] = 0.79

    with pytest.raises(ValidationError, match="thresholds are frozen"):
        StudySpec.model_validate(raw)


def test_protocol_rejects_wrong_seed_count() -> None:
    raw = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
    raw["stochastic"]["seeds"] = [11, 23, 42, 71]

    with pytest.raises(ValidationError, match="five unique seeds"):
        StudySpec.model_validate(raw)


def test_protocol_rejects_posthoc_hypothesis_criterion_relaxation() -> None:
    raw = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
    raw["hypothesis_decision_criteria"]["primary_min_pooled_v2_hard_pass_gain"] = 5

    with pytest.raises(ValidationError):
        StudySpec.model_validate(raw)


def test_protocol_rejects_unapproved_container_environment() -> None:
    raw = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
    raw["models"][0]["expected_container_environment"] = {"HF_TOKEN": "secret"}

    with pytest.raises(ValidationError, match="unapproved name or value"):
        StudySpec.model_validate(raw)
