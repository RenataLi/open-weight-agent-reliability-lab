from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_reliability_lab.agent.models import ChatTemplateMode
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
        "97768faf6f46f379b40ed029460e8e3de416a2cf77f11963fa244332f28c3466"
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
    assert first.cells[0].chat_template_mode is ChatTemplateMode.DISABLE_THINKING
    assert first.cells[-1].chat_template_mode is ChatTemplateMode.NATIVE


def test_protocol_hash_and_every_pinned_input_are_reproducible() -> None:
    spec = load_study_spec(PROTOCOL_PATH)

    verify_study_inputs(spec, PROJECT_ROOT)

    assert canonical_sha256(spec) == (
        "1d92852c8149a19c4b657bd3f59e82370f1612b752bfaaed1cf104ca102f731a"
    )
    assert spec.schema_version == "1.1"
    assert spec.study_id == "m3-controlled-contract-study-v1r1"
    assert spec.lineage.parent_study_id == "m3-controlled-contract-study-v1"
    assert spec.lineage.prospective_status == "not_pristine_first_pass_prospective"
    assert spec.lineage.prior_outcomes_excluded_from_r1 is True
    assert spec.dataset.fixture_worlds == 3
    assert spec.dataset.suite_sha256 == (
        "3659bb8bfcae05ae7215cb4bc8bae8e6b1b225a9ed0f17c85185ce6c4de19a99"
    )
    assert spec.dataset.prompt_set_sha256 == (
        "4e11bedb86ca59aadccc3b36fb1487e681edbe74c774ad8f014ef1655c8d1699"
    )
    assert spec.stochastic.seeds == (11, 23, 42, 71, 97)
    assert (spec.stochastic.temperature, spec.stochastic.top_p) == (0.3, 1.0)
    assert [model.revision for model in spec.models] == [
        "cc5442c03a5c0bff0bd4c6888d9a40029c637733",
        "f6fae9795746f63c9be8344932f01275f3c63734",
    ]
    assert [model.chat_template_mode for model in spec.models] == [
        ChatTemplateMode.DISABLE_THINKING,
        ChatTemplateMode.NATIVE,
    ]
    assert "chat_template_kwargs" in spec.models[0].expected_request_extra_body
    assert "chat_template_kwargs" not in spec.models[1].expected_request_extra_body
    assert [model.expected_request_extra_body_sha256 for model in spec.models] == [
        "a708e0e8efb2d85fcfb6e63358670bd57378384f1fcb315391b2630de3d49e45",
        "446637575e9a6dd899e6f42f507eb9c03ae16d3428fcfed938eda191d7ddc3c5",
    ]


def test_abort_record_discloses_complete_partial_and_unrun_cells() -> None:
    record = json.loads(
        (PROJECT_ROOT / "reports" / "m3-controlled-contract-study-v1-abort.json").read_text(
            encoding="utf-8"
        )
    )

    assert record["matrix_accounting"] == {
        "planned_cells": 24,
        "complete_cells": 1,
        "partial_cells": 1,
        "unrun_cells": 22,
        "eligible_cells": 0,
        "aggregate_conclusion_permitted": False,
        "promotion_conclusion_permitted": False,
    }
    assert record["first_complete_cell"]["passed_runs"] == 3
    assert record["first_complete_cell"]["excluded_from_repaired_study"] is True
    assert record["partial_second_cell"]["result_present"] is False
    assert record["repair"]["no_outcome_driven_changes"] is True
    assert record["original_study"]["archive_root"] == (
        "runs/m3-controlled-contract-study-v1-aborted-81d5395-20260809T044101"
    )


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


def test_protocol_rejects_native_mistral_with_qwen_template_kwargs() -> None:
    raw = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
    raw["models"][1]["expected_request_extra_body"]["chat_template_kwargs"] = {
        "enable_thinking": False
    }

    with pytest.raises(ValidationError, match="native chat-template mode must omit"):
        StudySpec.model_validate(raw)


def test_protocol_rejects_tampered_abort_record_hash() -> None:
    raw = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
    raw["lineage"]["abort_record_sha256"] = "0" * 64
    spec = StudySpec.model_validate(raw)

    with pytest.raises(ValueError, match="abort record does not match"):
        verify_study_inputs(spec, PROJECT_ROOT)
