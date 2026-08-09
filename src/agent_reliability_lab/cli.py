"""Public command line interface for the offline benchmark core."""

from __future__ import annotations

import json
import os
import platform
import re
import subprocess
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit, urlunsplit

import httpx
import typer
from pydantic import JsonValue, ValidationError

from agent_reliability_lab.agent.models import (
    AgentPolicy,
    AgentRunConfig,
    ChatTemplateMode,
    PromptContractVersion,
    build_request_extra_body,
)
from agent_reliability_lab.agent.persistence import RunStore
from agent_reliability_lab.agent.prompt_sets import load_prompt_set, validate_prompt_set
from agent_reliability_lab.agent.prompting import system_prompt
from agent_reliability_lab.agent.suite import run_agent_suite
from agent_reliability_lab.providers.base import ProviderError
from agent_reliability_lab.providers.openai_compatible import OpenAICompatibleProvider
from agent_reliability_lab.study import (
    StudyArtifactError,
    StudyPlan,
    StudySpec,
    build_study_plan,
    build_study_report,
    load_study_artifacts,
    load_study_cell_artifact,
    load_study_spec,
    render_markdown_report,
    verify_study_inputs,
)
from agent_reliability_lab.tasks.evaluator import load_suite, run_suite, validate_suite_contract
from agent_reliability_lab.tasks.models import BenchmarkSuite
from agent_reliability_lab.tools.registry import build_registry

app = typer.Typer(
    name="agent-reliability-lab",
    help="Deterministic reliability benchmark for open-weight tool-calling agents.",
    no_args_is_help=True,
)

DEFAULT_SUITE = Path("benchmarks/smoke_suite.json")
DEFAULT_PROMPTS = Path("benchmarks/model_prompts_v1.json")
DEFAULT_DEPLOYMENT_COMPOSE = Path("deploy/compose.gpu.yml")
DEFAULT_STUDY_PROTOCOL = Path("studies/m3_protocol.json")
DEFAULT_STUDY_CELLS = Path("runs/m3-controlled-contract-study-v1r2/cells")
agent_app = typer.Typer(help="Run real model-driven evaluations against the simulator.")
study_app = typer.Typer(help="Validate, plan, and aggregate a fixed model-study protocol.")
app.add_typer(agent_app, name="agent")
app.add_typer(study_app, name="study")


def _load_or_exit(path: Path) -> BenchmarkSuite:
    try:
        return load_suite(path)
    except FileNotFoundError:
        typer.echo(f"Benchmark file not found: {path}", err=True)
        raise typer.Exit(2) from None
    except (OSError, ValidationError, ValueError) as exc:
        typer.echo(f"Invalid benchmark: {exc}", err=True)
        raise typer.Exit(2) from None


@app.command()
def validate(
    path: Annotated[
        Path, typer.Argument(help="Path to a benchmark suite JSON file.")
    ] = DEFAULT_SUITE,
) -> None:
    """Validate schemas, references, tools, fixtures, and invariants."""

    suite = _load_or_exit(path)
    try:
        validate_suite_contract(suite)
    except ValueError as exc:
        typer.echo(f"Invalid benchmark contract: {exc}", err=True)
        raise typer.Exit(2) from None
    typer.echo(f"Validated {suite.suite_id}: {len(suite.tasks)} tasks")


@app.command()
def smoke(
    path: Annotated[
        Path, typer.Argument(help="Path to the smoke suite JSON file.")
    ] = DEFAULT_SUITE,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit only the machine-readable result."),
    ] = False,
) -> None:
    """Run reference trajectories and exact deterministic evaluation."""

    suite = _load_or_exit(path)
    try:
        result = run_suite(suite)
    except ValueError as exc:
        typer.echo(f"Invalid benchmark contract: {exc}", err=True)
        raise typer.Exit(2) from None
    if json_output:
        typer.echo(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, sort_keys=True))
    else:
        typer.echo(f"{result.suite_id}: {result.passed_tasks}/{result.total_tasks} tasks passed")
        for task in result.tasks:
            marker = "PASS" if task.passed else "FAIL"
            typer.echo(f"  [{marker}] {task.task_id}")
            if not task.passed:
                for check in task.checks:
                    if not check.passed:
                        typer.echo(
                            f"    {check.name}: expected={check.expected!r} "
                            f"observed={check.observed!r}"
                        )
    if not result.passed:
        raise typer.Exit(1)


@app.command("tool-schemas")
def tool_schemas() -> None:
    """Print stable input schemas for every simulator tool."""

    registry = build_registry()
    schemas = [registry[name].json_schema() for name in sorted(registry)]
    typer.echo(json.dumps(schemas, ensure_ascii=False, sort_keys=True))


def _safe_base_url(base_url: str) -> str:
    value = base_url.strip()
    if not value:
        raise ValueError("base_url must not be empty")
    if "\\" in value or any(character.isspace() or ord(character) < 32 for character in value):
        raise ValueError("base_url is malformed")
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("base_url is malformed") from exc
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("base_url must use http or https")
    if parsed.hostname is None:
        raise ValueError("base_url must include a host")
    try:
        normalized_hostname = parsed.hostname.rstrip(".").encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("base_url host is malformed") from exc
    if not normalized_hostname or any(not label for label in normalized_hostname.split(".")):
        raise ValueError("base_url host is malformed")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("base_url must not contain credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("base_url must not contain a query or fragment")
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _file_digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _json_digest(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _git_provenance(project_root: Path) -> tuple[str | None, bool | None]:
    try:
        commit_process = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        status_process = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=normal"],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None, None
    commit = commit_process.stdout.strip()
    return (commit or None), bool(status_process.stdout.strip())


def _command_output(arguments: list[str], *, cwd: Path | None = None) -> str | None:
    try:
        process = subprocess.run(
            arguments,
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = process.stdout.strip()
    return value or None


def _compose_image_reference(compose_path: Path) -> str | None:
    if not compose_path.is_file():
        return None
    for line in compose_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("image:"):
            return stripped.partition(":")[2].strip() or None
    return None


def _resolve_project_file(project_root: Path, path: Path, *, label: str) -> Path:
    """Resolve a declared project input without allowing provenance to escape the repository."""

    root = project_root.resolve()
    candidate = (root / path).resolve() if not path.is_absolute() else path.resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} must be inside the project directory") from exc
    if not candidate.is_file():
        raise ValueError(f"{label} file not found: {path}")
    return candidate


def _docker_model_provenance() -> dict[str, JsonValue]:
    container = "agent-reliability-vllm"
    image_id = _command_output(["docker", "inspect", "--format", "{{.Image}}", container])
    configured_image = _command_output(
        ["docker", "inspect", "--format", "{{.Config.Image}}", container]
    )
    command_json = _command_output(
        ["docker", "inspect", "--format", "{{json .Config.Cmd}}", container]
    )
    environment_json = _command_output(
        ["docker", "inspect", "--format", "{{json .Config.Env}}", container]
    )
    safe_arguments = _safe_vllm_arguments(command_json)
    safe_environment = _safe_container_environment(environment_json)
    command_sha256 = _sanitized_container_command_sha256(command_json)
    repo_digests_json = (
        _command_output(
            ["docker", "image", "inspect", image_id, "--format", "{{json .RepoDigests}}"]
        )
        if image_id is not None
        else None
    )
    return {
        "observed_container_image_id": image_id,
        "observed_container_image_reference": configured_image,
        "observed_container_command_sha256": command_sha256,
        "observed_container_environment": safe_environment,
        "observed_container_environment_sha256": (
            _json_digest(safe_environment) if safe_environment is not None else None
        ),
        "observed_vllm_arguments": safe_arguments or None,
        "observed_vllm_arguments_sha256": (
            _json_digest(safe_arguments) if safe_arguments else None
        ),
        "observed_image_repo_digests_json": repo_digests_json,
    }


def _safe_container_environment(environment_json: str | None) -> dict[str, JsonValue] | None:
    """Return only non-secret container settings that change the pinned inference behavior."""

    if environment_json is None:
        return None
    try:
        environment = json.loads(environment_json)
    except ValueError:
        return None
    if not isinstance(environment, list) or not all(isinstance(item, str) for item in environment):
        return None

    allowed_values = {"VLLM_USE_V2_MODEL_RUNNER": {"0", "1"}}
    observed: dict[str, JsonValue] = {}
    for item in environment:
        name, separator, value = item.partition("=")
        if name not in allowed_values:
            continue
        if not separator or value not in allowed_values[name] or name in observed:
            return None
        observed[name] = value
    return observed


def _safe_vllm_arguments(command_json: str | None) -> dict[str, JsonValue]:
    """Allowlist reproducibility flags without persisting credentials from Docker Cmd."""

    if command_json is None:
        return {}
    try:
        command = json.loads(command_json)
    except ValueError:
        return {}
    if not isinstance(command, list) or not all(isinstance(item, str) for item in command):
        return {}
    value_flags = {
        "--model",
        "--revision",
        "--tokenizer-revision",
        "--tokenizer-mode",
        "--config-format",
        "--load-format",
        "--served-model-name",
        "--dtype",
        "--max-model-len",
        "--gpu-memory-utilization",
        "--max-num-seqs",
        "--max-num-batched-tokens",
        "--generation-config",
        "--tool-call-parser",
        "--reasoning-parser",
        "--seed",
        "--host",
        "--port",
    }
    boolean_flags = {
        "--language-model-only",
        "--enable-auto-tool-choice",
        "--enable-prefix-caching",
        "--no-enable-prefix-caching",
    }
    observed: dict[str, JsonValue] = {}
    index = 0
    while index < len(command):
        item = command[index]
        if item in value_flags and index + 1 < len(command):
            observed[item.removeprefix("--")] = command[index + 1]
            index += 2
            continue
        if item in boolean_flags:
            observed[item.removeprefix("--")] = True
        index += 1
    return observed


def _sanitized_container_command_sha256(command_json: str | None) -> str | None:
    """Hash complete Docker argv while replacing credential values with a fixed sentinel."""

    if command_json is None:
        return None
    try:
        command = json.loads(command_json)
    except ValueError:
        return None
    if (
        not isinstance(command, list)
        or not command
        or not all(isinstance(item, str) for item in command)
    ):
        return None

    def sensitive(flag: str) -> bool:
        normalized = flag.casefold().lstrip("-").split("=", 1)[0]
        segments = set(re.split(r"[-_]", normalized))
        return bool(
            "password" in segments
            or "secret" in segments
            or "credential" in segments
            or "auth" in segments
            or normalized in {"api-key", "api_key", "token", "access-token", "auth-token"}
            or normalized.endswith(("-token", "_token"))
        )

    sanitized = list(command)
    index = 0
    while index < len(sanitized):
        item = sanitized[index]
        if item.startswith("-") and "=" in item:
            flag, _, _ = item.partition("=")
            if sensitive(flag):
                sanitized[index] = f"{flag}=<redacted>"
            index += 1
            continue
        if item.startswith("-") and sensitive(item):
            if index + 1 >= len(sanitized) or sanitized[index + 1].startswith("-"):
                return None
            sanitized[index + 1] = "<redacted>"
            index += 2
            continue
        index += 1
    return _json_digest(sanitized)


def _vllm_server_version(base_url: str, api_key: str | None) -> str | None:
    parsed = urlsplit(base_url)
    endpoint = urlunsplit((parsed.scheme, parsed.netloc, "/version", "", ""))
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
    try:
        with httpx.Client(timeout=5.0, trust_env=False, headers=headers) as client:
            response = client.get(endpoint)
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    version = payload.get("version")
    return version if isinstance(version, str) and version else None


def _tool_schema_digest() -> str:
    registry = build_registry()
    schemas = [registry[name].json_schema() for name in sorted(registry)]
    return _json_digest(schemas)


def _load_verified_study(protocol_path: Path) -> tuple[StudySpec, StudyPlan]:
    spec = load_study_spec(protocol_path)
    verify_study_inputs(spec, _project_root())
    return spec, build_study_plan(spec)


def _write_immutable_text(path: Path, content: str) -> None:
    """Create an immutable generated artifact, accepting only a byte-identical repeat."""

    if path.is_file():
        if path.read_text(encoding="utf-8") != content:
            raise ValueError(f"refusing to overwrite a different artifact: {path}")
        return
    if path.exists():
        raise ValueError(f"artifact path is not a file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@study_app.command("validate")
def study_validate(
    protocol_path: Annotated[
        Path,
        typer.Argument(help="Preregistered study protocol JSON."),
    ] = DEFAULT_STUDY_PROTOCOL,
) -> None:
    """Verify every frozen input and expand the exact study matrix."""

    try:
        spec, plan = _load_verified_study(protocol_path)
    except (FileNotFoundError, OSError, ValidationError, ValueError) as exc:
        typer.echo(f"Invalid study protocol: {exc}", err=True)
        raise typer.Exit(2) from None
    typer.echo(
        f"Validated {spec.study_id}: {len(plan.cells)} cells, "
        f"{plan.stochastic_task_runs} scored + {plan.greedy_anchor_task_runs} descriptive runs"
    )
    typer.echo(f"Protocol SHA-256: {plan.protocol_sha256}")


@study_app.command("plan")
def study_plan(
    protocol_path: Annotated[
        Path,
        typer.Argument(help="Preregistered study protocol JSON."),
    ] = DEFAULT_STUDY_PROTOCOL,
    output: Annotated[
        Path | None,
        typer.Option("--output", help="Write the immutable expanded plan JSON."),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit the complete plan to stdout."),
    ] = False,
) -> None:
    """Expand the protocol-defined Cartesian matrix in execution order."""

    try:
        _, plan = _load_verified_study(protocol_path)
        payload = json.dumps(
            plan.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        if output is not None:
            _write_immutable_text(output, f"{payload}\n")
    except (FileNotFoundError, OSError, ValidationError, ValueError) as exc:
        typer.echo(f"Cannot build study plan: {exc}", err=True)
        raise typer.Exit(2) from None
    if json_output:
        typer.echo(payload)
    else:
        typer.echo(f"Planned {len(plan.cells)} immutable cells ({plan.total_task_runs} task runs)")
        if output is not None:
            typer.echo(f"Plan: {output}")


@study_app.command("report")
def study_report(
    protocol_path: Annotated[
        Path,
        typer.Argument(help="Preregistered study protocol JSON."),
    ] = DEFAULT_STUDY_PROTOCOL,
    cells_dir: Annotated[
        Path,
        typer.Option("--cells-dir", help="Directory containing immutable per-cell artifacts."),
    ] = DEFAULT_STUDY_CELLS,
    output_dir: Annotated[
        Path | None,
        typer.Option("--output-dir", help="Destination for aggregate JSON and Markdown."),
    ] = None,
) -> None:
    """Fail closed on a partial matrix, then compute fixed gates and paired metrics."""

    try:
        spec, plan = _load_verified_study(protocol_path)
        artifacts = load_study_artifacts(spec, plan, cells_dir)
        report = build_study_report(spec, plan, artifacts)
        destination = output_dir or cells_dir.parent
        result_payload = json.dumps(
            report.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        _write_immutable_text(destination / "study-result.json", f"{result_payload}\n")
        _write_immutable_text(destination / "report.md", render_markdown_report(report))
    except (
        FileNotFoundError,
        OSError,
        StudyArtifactError,
        ValidationError,
        ValueError,
    ) as exc:
        typer.echo(f"Cannot aggregate study: {exc}", err=True)
        raise typer.Exit(2) from None
    typer.echo(
        f"M3 promotion gate: {'PASS' if report.promotion_passed else 'FAIL'}; "
        f"artifacts: {destination}"
    )
    if not report.promotion_passed:
        raise typer.Exit(1)


@study_app.command("validate-cell")
def study_validate_cell(
    cell_id: Annotated[str, typer.Argument(help="Exact cell ID from the frozen plan.")],
    protocol_path: Annotated[
        Path,
        typer.Option("--protocol", help="Preregistered study protocol JSON."),
    ] = DEFAULT_STUDY_PROTOCOL,
    cells_dir: Annotated[
        Path,
        typer.Option("--cells-dir", help="Directory containing immutable per-cell artifacts."),
    ] = DEFAULT_STUDY_CELLS,
) -> None:
    """Validate one completed cell before a resume operation may skip it."""

    try:
        spec, plan = _load_verified_study(protocol_path)
        cell = next((item for item in plan.cells if item.cell_id == cell_id), None)
        if cell is None:
            raise ValueError(f"cell is not in the frozen plan: {cell_id}")
        artifact = load_study_cell_artifact(spec, cell, cells_dir)
    except (
        FileNotFoundError,
        OSError,
        StudyArtifactError,
        ValidationError,
        ValueError,
    ) as exc:
        typer.echo(f"Invalid study cell: {exc}", err=True)
        raise typer.Exit(2) from None
    typer.echo(
        f"Validated {cell_id}: {artifact.result.passed_runs}/{artifact.result.total_runs} "
        f"task outcomes passed"
    )


@agent_app.command("run")
def agent_run(
    suite_path: Annotated[Path, typer.Argument(help="Benchmark suite JSON.")] = DEFAULT_SUITE,
    prompt_set_path: Annotated[
        Path,
        typer.Option("--prompts", help="Agent-facing prompt set JSON."),
    ] = DEFAULT_PROMPTS,
    deployment_compose: Annotated[
        Path,
        typer.Option(
            "--deployment-compose",
            help="Project-relative Compose file used to serve the selected model.",
        ),
    ] = DEFAULT_DEPLOYMENT_COMPOSE,
    task_id: Annotated[
        str | None,
        typer.Option("--task-id", help="Run one prompt task as a canary."),
    ] = None,
    base_url: Annotated[
        str,
        typer.Option("--base-url", help="OpenAI-compatible API base URL."),
    ] = "http://127.0.0.1:8000/v1",
    model: Annotated[str, typer.Option("--model", help="Served model identifier.")] = "local-model",
    model_revision: Annotated[
        str | None,
        typer.Option("--model-revision", help="Immutable model revision recorded in artifacts."),
    ] = None,
    policy: Annotated[AgentPolicy, typer.Option("--policy")] = AgentPolicy.POLICY_AWARE,
    contract_version: Annotated[
        PromptContractVersion | None,
        typer.Option(
            "--contract-version",
            help="Override the versioned system reporting contract declared by the prompt set.",
        ),
    ] = None,
    study_id: Annotated[
        str | None,
        typer.Option("--study-id", help="Preregistered study identity recorded in the manifest."),
    ] = None,
    study_protocol_sha256: Annotated[
        str | None,
        typer.Option(
            "--study-protocol-sha256",
            help="Canonical fixed-protocol hash recorded in the manifest.",
        ),
    ] = None,
    chat_template_mode: Annotated[
        ChatTemplateMode,
        typer.Option(
            "--chat-template-mode",
            help=(
                "Explicit request profile: disable_thinking sends Qwen's template option; "
                "native omits chat_template_kwargs."
            ),
        ),
    ] = ChatTemplateMode.DISABLE_THINKING,
    repetitions: Annotated[int, typer.Option("--repetitions", min=1)] = 1,
    seed: Annotated[int, typer.Option("--seed", min=0)] = 42,
    temperature: Annotated[float, typer.Option("--temperature", min=0.0, max=2.0)] = 0.0,
    top_p: Annotated[float, typer.Option("--top-p", min=0.001, max=1.0)] = 1.0,
    max_tokens: Annotated[int, typer.Option("--max-tokens", min=1)] = 2048,
    max_turns: Annotated[int, typer.Option("--max-turns", min=1)] = 12,
    timeout_seconds: Annotated[float, typer.Option("--timeout-seconds", min=1.0)] = 300.0,
    provider_attempts: Annotated[int, typer.Option("--provider-attempts", min=1)] = 2,
    api_key_env: Annotated[
        str,
        typer.Option("--api-key-env", help="Environment variable containing the API key."),
    ] = "VLLM_API_KEY",
    output_dir: Annotated[Path, typer.Option("--output-dir")] = Path("runs"),
    run_id: Annotated[str | None, typer.Option("--run-id")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Evaluate a local tool-calling model and persist complete run artifacts."""

    try:
        safe_url = _safe_base_url(base_url)
        project_root = _project_root()
        compose_path = _resolve_project_file(
            project_root,
            deployment_compose,
            label="deployment compose",
        )
        normalized_model = model.strip()
        if not normalized_model:
            raise ValueError("model must not be empty")
        if (study_id is None) != (study_protocol_sha256 is None):
            raise ValueError("study-id and study-protocol-sha256 must be supplied together")
        if study_id is not None and re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", study_id) is None:
            raise ValueError("study-id must be a lowercase hyphenated slug")
        if (
            study_protocol_sha256 is not None
            and re.fullmatch(r"[0-9a-f]{64}", study_protocol_sha256) is None
        ):
            raise ValueError("study-protocol-sha256 must be a lowercase SHA-256")
        suite = load_suite(suite_path)
        validate_suite_contract(suite)
        prompt_set = load_prompt_set(prompt_set_path)
        validate_prompt_set(prompt_set, suite)
        if contract_version is not None:
            prompt_set = prompt_set.model_copy(update={"contract_version": contract_version})
        if task_id is not None:
            matching_tasks = tuple(task for task in suite.tasks if task.task_id == task_id)
            prompt = prompt_set.tasks.get(task_id)
            if not matching_tasks or prompt is None:
                raise ValueError(f"unknown task-id: {task_id}")
            suite = suite.model_copy(update={"tasks": matching_tasks})
            prompt_set = prompt_set.model_copy(update={"tasks": {task_id: prompt}})
        config = AgentRunConfig(
            policy=policy,
            seed=seed,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
            max_turns=max_turns,
            chat_template_mode=chat_template_mode,
        )
        request_extra_body = build_request_extra_body(config)
        resolved_run_id = run_id or datetime.now(UTC).strftime("run-%Y%m%dT%H%M%S.%fZ")
        store = RunStore(output_dir, resolved_run_id)
        provider_backoff_seconds = 0.25
        api_key = os.getenv(api_key_env)
        provider = OpenAICompatibleProvider(
            base_url=safe_url,
            model=normalized_model,
            api_key=api_key,
            timeout=timeout_seconds,
            transport_retries=provider_attempts - 1,
            backoff_seconds=provider_backoff_seconds,
            seed=seed,
            temperature=temperature,
        )
    except (FileNotFoundError, OSError, ValidationError, ValueError) as exc:
        typer.echo(f"Invalid agent run configuration: {exc}", err=True)
        raise typer.Exit(2) from None

    try:
        try:
            observed_model_ids = provider.check_model_available()
        except ProviderError as exc:
            typer.echo(
                f"Model preflight failed ({exc.code.value}): {exc.message}",
                err=True,
            )
            raise typer.Exit(3) from None

        try:
            uv_lock = project_root / "uv.lock"
            git_commit, git_dirty = _git_provenance(project_root)
            docker_provenance = _docker_model_provenance()
            observed_vllm_arguments = docker_provenance["observed_vllm_arguments"]
            manifest: dict[str, JsonValue] = {
                "schema_version": "1.0",
                "run_id": resolved_run_id,
                "study_id": study_id,
                "study_protocol_sha256": study_protocol_sha256,
                "suite_id": suite.suite_id,
                "suite_sha256": _file_digest(suite_path),
                "prompt_set_id": prompt_set.prompt_set_id,
                "prompt_set_sha256": _file_digest(prompt_set_path),
                "contract_version": prompt_set.contract_version,
                "chat_template_mode": config.chat_template_mode,
                "request_extra_body": request_extra_body,
                "request_extra_body_sha256": _json_digest(request_extra_body),
                "system_prompt_sha256": _json_digest(
                    system_prompt(policy, prompt_set.contract_version)
                ),
                "tool_schemas_sha256": _tool_schema_digest(),
                "uv_lock_sha256": _file_digest(uv_lock) if uv_lock.is_file() else None,
                "git_commit": git_commit,
                "git_dirty": git_dirty,
                "python_version": platform.python_version(),
                "platform": platform.platform(),
                "gpu_inventory": _command_output(
                    [
                        "nvidia-smi",
                        "--query-gpu=name,driver_version,memory.total,compute_cap",
                        "--format=csv,noheader,nounits",
                    ]
                ),
                "docker_server_version": _command_output(
                    ["docker", "version", "--format", "{{.Server.Version}}"]
                ),
                "vllm_image_reference": _compose_image_reference(compose_path),
                "deployment_compose_path": compose_path.relative_to(project_root).as_posix(),
                "deployment_compose_sha256": _file_digest(compose_path),
                "task_id": task_id,
                "model": normalized_model,
                "model_revision": model_revision,
                "declared_model_revision": model_revision,
                "observed_model_ids": list(observed_model_ids),
                "model_identity_verified": normalized_model in observed_model_ids,
                "model_revision_observed_in_container_command": (
                    isinstance(observed_vllm_arguments, dict)
                    and model_revision is not None
                    and observed_vllm_arguments.get("revision") == model_revision
                ),
                "observed_vllm_version": _vllm_server_version(safe_url, api_key),
                **docker_provenance,
                "provider_base_url": safe_url,
                "policy": policy,
                "repetitions": repetitions,
                "seed": seed,
                "temperature": temperature,
                "top_p": top_p,
                "max_tokens": max_tokens,
                "max_turns": max_turns,
                "timeout_seconds": timeout_seconds,
                "provider_attempts": provider_attempts,
                "provider_backoff_seconds": provider_backoff_seconds,
                "api_key_present": bool(api_key),
            }
            store.start(manifest)
            result = run_agent_suite(
                suite,
                prompt_set,
                provider,
                config,
                repetitions=repetitions,
                provider_base_url=safe_url,
                model_revision=model_revision,
                event_sink_factory=lambda selected_task_id, repetition: (
                    lambda event: store.append(
                        {
                            "task_id": selected_task_id,
                            "repetition": repetition,
                            **event,
                        }
                    )
                ),
            )
            result_path = store.finish(result)
        except (OSError, RuntimeError, ValidationError, ValueError, ProviderError) as exc:
            typer.echo(f"Agent run infrastructure failure: {exc}", err=True)
            raise typer.Exit(3) from None
    finally:
        provider.close()

    if json_output:
        typer.echo(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, sort_keys=True))
    else:
        typer.echo(
            f"{result.model}: {result.passed_runs}/{result.total_runs} runs passed "
            f"({result.aggregate['pass_rate']:.1%})"
        )
        typer.echo(f"Artifacts: {result_path}")
    if result.aggregate["infrastructure_failures"]:
        raise typer.Exit(3)
    if not result.passed:
        raise typer.Exit(1)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
