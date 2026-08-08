from __future__ import annotations

from pathlib import Path

from agent_reliability_lab.agent.models import PromptContractVersion
from agent_reliability_lab.agent.prompt_sets import load_prompt_set, validate_prompt_set
from agent_reliability_lab.tasks.evaluator import load_suite, run_suite
from agent_reliability_lab.tasks.models import BenchmarkSuite

ROOT = Path(__file__).parents[2]
DEV_SUITE = ROOT / "benchmarks" / "smoke_suite.json"
HOLDOUT_SUITE = ROOT / "benchmarks" / "m3_holdout_suite.json"
HOLDOUT_PROMPTS = ROOT / "benchmarks" / "m3_holdout_prompts.json"


def _entity_ids(suite: BenchmarkSuite) -> dict[str, set[str]]:
    entities = {
        "services": set[str](),
        "deployments": set[str](),
        "data_sources": set[str](),
        "incidents": set[str](),
        "approvals": set[str](),
    }
    for fixture in suite.fixtures.values():
        entities["services"].update(fixture.world.services)
        entities["deployments"].update(fixture.world.deployments)
        entities["data_sources"].update(fixture.world.data_sources)
        entities["incidents"].update(fixture.world.incidents)
        entities["approvals"].update(fixture.world.approvals)
    return entities


def test_m3_reference_trajectories_pass_and_replay_bitwise() -> None:
    suite = load_suite(HOLDOUT_SUITE)

    first = run_suite(suite)
    second = run_suite(suite)

    assert first.passed
    assert first.passed_tasks == 8
    assert first.model_dump_json() == second.model_dump_json()


def test_m3_prompts_are_complete_opaque_and_have_recovery_slack() -> None:
    suite = load_suite(HOLDOUT_SUITE)
    prompts = load_prompt_set(HOLDOUT_PROMPTS)

    validate_prompt_set(prompts, suite)
    assert set(prompts.tasks) == {task.task_id for task in suite.tasks}
    assert prompts.contract_version is PromptContractVersion.V1
    handles = [prompt.case_handle for prompt in prompts.tasks.values()]
    assert all(handle is not None for handle in handles)
    assert len(handles) == len(set(handles))
    for task in suite.tasks:
        budget = prompts.tasks[task.task_id].max_tool_calls
        assert budget is not None
        assert budget >= len(task.reference_trajectory) + 2


def test_m3_entities_are_disjoint_from_development_suite() -> None:
    development = _entity_ids(load_suite(DEV_SUITE))
    holdout = _entity_ids(load_suite(HOLDOUT_SUITE))

    for category in development:
        assert development[category].isdisjoint(holdout[category]), category


def test_contract_axis_does_not_change_task_prompts() -> None:
    prompts = load_prompt_set(HOLDOUT_PROMPTS)
    v2 = prompts.model_copy(update={"contract_version": PromptContractVersion.V2})

    assert prompts.tasks == v2.tasks
    assert prompts.prompt_set_id == v2.prompt_set_id
