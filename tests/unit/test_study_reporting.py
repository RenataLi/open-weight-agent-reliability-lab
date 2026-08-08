from __future__ import annotations

from pathlib import Path

import pytest

from agent_reliability_lab.agent.models import (
    AgentPolicy,
    AgentSuiteResult,
    AgentTaskResult,
    ExecutedToolCall,
    TerminationReason,
)
from agent_reliability_lab.domain.events import (
    PolicyDecision,
    ToolEffect,
    ToolResult,
    ToolStatus,
    ToolTrace,
)
from agent_reliability_lab.study import (
    build_study_plan,
    build_study_report,
    load_study_spec,
    render_markdown_report,
)
from agent_reliability_lab.study.models import (
    CellArtifact,
    HypothesisName,
    StudyCell,
    StudyPhase,
)
from agent_reliability_lab.tasks.models import EvaluationCheck

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _task_result(task_id: str, *, passed: bool, duplicate_commit: bool = False) -> AgentTaskResult:
    read_result = ToolResult(
        call_id="call-read",
        tool_name="get_incident",
        status=ToolStatus.OK,
        data={"incident_id": "case"},
        state_revision=0,
        state_hash="state",
    )
    report_result = ToolResult(
        call_id="call-report",
        tool_name="submit_incident_report",
        status=ToolStatus.OK,
        data={"accepted": True},
        state_revision=0,
        state_hash="state",
    )
    expected_commits = 1 if duplicate_commit else 0
    observed_commits = 2 if duplicate_commit else 0
    return AgentTaskResult(
        task_id=task_id,
        repetition=1,
        passed=passed,
        termination_reason=TerminationReason.REPORT_SUBMITTED,
        checks=(
            EvaluationCheck(
                name="report_semantics",
                passed=True,
                expected={"cause": "expected"},
                observed={"cause": "expected"},
            ),
            EvaluationCheck(
                name="evidence_grounded",
                passed=True,
                expected=True,
                observed=True,
            ),
            EvaluationCheck(
                name="committed_effects",
                passed=not duplicate_commit,
                expected=expected_commits,
                observed=observed_commits,
            ),
            EvaluationCheck(
                name="unauthorized_commits",
                passed=True,
                expected=0,
                observed=0,
            ),
        ),
        model_turns=(),
        tool_calls=(
            ExecutedToolCall(
                turn=1,
                provider_call_id="provider-read",
                tool_name="get_incident",
                raw_arguments="{}",
                arguments={},
                result=read_result,
            ),
            ExecutedToolCall(
                turn=2,
                provider_call_id="provider-report",
                tool_name="submit_incident_report",
                raw_arguments='{"evidence_call_ids":["call-read"]}',
                arguments={"evidence_call_ids": ["call-read"]},
                result=report_result,
            ),
        ),
        simulator_trace=(
            ToolTrace(
                sequence=1,
                call_id="call-read",
                tool_name="get_incident",
                effect=ToolEffect.READ,
                status=ToolStatus.OK,
                request_digest="request",
                state_before_hash="state",
                state_after_hash="state",
                state_revision=0,
                policy_decision=PolicyDecision.NOT_APPLICABLE,
            ),
            ToolTrace(
                sequence=2,
                call_id="call-report",
                tool_name="submit_incident_report",
                effect=ToolEffect.CONTROL,
                status=ToolStatus.OK,
                request_digest="report",
                state_before_hash="state",
                state_after_hash="state",
                state_revision=0,
                policy_decision=PolicyDecision.NOT_APPLICABLE,
            ),
        ),
        final_state_hash="state",
        trace_hash="trace",
        world_revision=0,
        total_latency_ms=1.0,
        prompt_tokens=1,
        completion_tokens=1,
        unsafe_action_attempts=0,
        committed_effects=observed_commits,
        replayed_calls=0,
        provider_attempts=2,
        provider_retries=0,
    )


def _passes(cell: StudyCell, task_id: str) -> bool:
    if cell.phase is StudyPhase.GREEDY_ANCHOR:
        return True
    if cell.contract_version.value == "v1":
        return not (task_id.endswith("DIAGNOSIS") and cell.seed in {11, 23})
    return not (task_id.endswith("QUARANTINE") and cell.seed == 97)


def _artifact(
    cell: StudyCell,
    *,
    duplicate_commit: bool = False,
    force_all_pass: bool = False,
) -> CellArtifact:
    tasks = tuple(
        _task_result(
            task_id,
            passed=True if force_all_pass else _passes(cell, task_id),
            duplicate_commit=duplicate_commit and index == 0,
        )
        for index, task_id in enumerate(cell.task_ids)
    )
    result = AgentSuiteResult(
        suite_id="m3-prospective-holdout-v1",
        prompt_set_id="m3-prospective-prompts-v1",
        contract_version=cell.contract_version,
        model=cell.served_model_name,
        model_revision=cell.model_revision,
        provider_base_url="http://127.0.0.1:8000/v1",
        policy=AgentPolicy.POLICY_AWARE,
        repetitions=1,
        passed=all(task.passed for task in tasks),
        passed_runs=sum(task.passed for task in tasks),
        total_runs=8,
        expected_runs=8,
        task_results=tasks,
        aggregate={},
    )
    return CellArtifact(
        cell=cell,
        git_commit="c" * 40,
        manifest_sha256="a" * 64,
        events_sha256="d" * 64,
        result_sha256="b" * 64,
        result=result,
    )


def _complete_artifacts(
    *,
    duplicate_commit: bool = False,
    force_all_pass: bool = False,
) -> tuple[CellArtifact, ...]:
    spec = load_study_spec(PROJECT_ROOT / "studies" / "m3_protocol.json")
    plan = build_study_plan(spec)
    return tuple(
        _artifact(
            cell,
            duplicate_commit=duplicate_commit and index == 0,
            force_all_pass=force_all_pass,
        )
        for index, cell in enumerate(plan.cells)
    )


def _with_candidate_regression(
    artifacts: tuple[CellArtifact, ...],
    *,
    unsafe: bool,
    policy_invalid: bool,
) -> tuple[CellArtifact, ...]:
    updated = list(artifacts)
    for index, artifact in enumerate(updated):
        if (
            artifact.cell.phase is not StudyPhase.STOCHASTIC
            or artifact.cell.contract_version.value != "v2"
        ):
            continue
        tasks = list(artifact.result.task_results)
        task = tasks[0]
        changes: dict[str, object] = {}
        if unsafe:
            changes["unsafe_action_attempts"] = 1
        if policy_invalid:
            calls = list(task.tool_calls)
            first_result = calls[0].result
            assert first_result is not None
            calls[0] = calls[0].model_copy(
                update={
                    "result": first_result.model_copy(update={"status": ToolStatus.POLICY_DENIED})
                }
            )
            changes["tool_calls"] = tuple(calls)
        tasks[0] = task.model_copy(update=changes)
        result = artifact.result.model_copy(update={"task_results": tuple(tasks)})
        updated[index] = artifact.model_copy(update={"result": result})
        break
    return tuple(updated)


def test_report_uses_paired_contract_metrics_and_excludes_greedy_from_gates() -> None:
    spec = load_study_spec(PROJECT_ROOT / "studies" / "m3_protocol.json")
    plan = build_study_plan(spec)

    report = build_study_report(spec, plan, _complete_artifacts())

    assert report.observed_stochastic_task_runs == 160
    assert report.observed_greedy_anchor_task_runs == 32
    assert report.promotion_passed
    assert len(report.paired_comparisons) == 2
    comparison = report.paired_comparisons[0]
    assert comparison.paired_task_runs == 40
    assert comparison.generalization_units == 8
    assert comparison.v2_wins == 4
    assert comparison.v2_losses == 1
    assert comparison.pass_rate_delta == pytest.approx(3 / 40)
    assert "No IID confidence interval" in comparison.inference_note
    assert "Greedy-anchor task runs: 32" in render_markdown_report(report)
    assert report.hypothesis_verdicts == {
        HypothesisName.PRIMARY: True,
        HypothesisName.CROSS_MODEL: True,
        HypothesisName.SAFETY: True,
    }


def test_promotion_can_pass_without_supporting_improvement_hypotheses() -> None:
    spec = load_study_spec(PROJECT_ROOT / "studies" / "m3_protocol.json")
    plan = build_study_plan(spec)

    report = build_study_report(
        spec,
        plan,
        _complete_artifacts(force_all_pass=True),
    )

    assert report.promotion_passed
    assert not report.hypothesis_verdicts[HypothesisName.PRIMARY]
    assert not report.hypothesis_verdicts[HypothesisName.CROSS_MODEL]
    assert report.hypothesis_verdicts[HypothesisName.SAFETY]


@pytest.mark.parametrize(
    ("unsafe", "policy_invalid", "safety_supported"),
    [(True, False, False), (False, True, True)],
)
def test_primary_hypothesis_requires_v2_safety_and_policy_validity(
    unsafe: bool,
    policy_invalid: bool,
    safety_supported: bool,
) -> None:
    spec = load_study_spec(PROJECT_ROOT / "studies" / "m3_protocol.json")
    plan = build_study_plan(spec)
    artifacts = _with_candidate_regression(
        _complete_artifacts(),
        unsafe=unsafe,
        policy_invalid=policy_invalid,
    )

    report = build_study_report(spec, plan, artifacts)

    primary = next(
        check for check in report.hypothesis_checks if check.hypothesis is HypothesisName.PRIMARY
    )
    assert isinstance(primary.observed, dict)
    assert primary.observed["pooled_hard_pass_gain"] == 6
    assert not primary.supported
    assert report.hypothesis_verdicts[HypothesisName.SAFETY] is safety_supported


def test_report_rejects_partial_or_altered_artifact_set() -> None:
    spec = load_study_spec(PROJECT_ROOT / "studies" / "m3_protocol.json")
    plan = build_study_plan(spec)
    artifacts = _complete_artifacts()

    with pytest.raises(ValueError, match="does not match plan"):
        build_study_report(spec, plan, artifacts[:-1])

    altered = artifacts[0].model_copy(
        update={
            "cell": artifacts[0].cell.model_copy(update={"seed": 999}),
        }
    )
    with pytest.raises(ValueError, match="metadata was altered"):
        build_study_report(spec, plan, (altered, *artifacts[1:]))

    other_commit = artifacts[-1].model_copy(update={"git_commit": "e" * 40})
    with pytest.raises(ValueError, match="one identical git commit"):
        build_study_report(spec, plan, (*artifacts[:-1], other_commit))


def test_duplicate_commit_regression_fails_zero_tolerance_gate() -> None:
    spec = load_study_spec(PROJECT_ROOT / "studies" / "m3_protocol.json")
    plan = build_study_plan(spec)

    report = build_study_report(spec, plan, _complete_artifacts(duplicate_commit=True))

    duplicate_gate = next(
        check for check in report.gate_checks if check.name == "duplicate_commits"
    )
    assert duplicate_gate.observed == 1
    assert not duplicate_gate.passed
    assert not report.promotion_passed
