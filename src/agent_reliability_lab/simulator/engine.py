"""Atomic deterministic tool execution with policy, faults, and idempotency."""

from __future__ import annotations

import json
from collections import Counter
from typing import Protocol, cast

from pydantic import JsonValue, ValidationError

from agent_reliability_lab.domain.events import (
    DomainEvent,
    ErrorDetail,
    FaultKind,
    FaultPhase,
    PolicyDecision,
    ToolEffect,
    ToolResult,
    ToolStatus,
    ToolTrace,
)
from agent_reliability_lab.domain.intents import ActionIntent
from agent_reliability_lab.domain.models import (
    ApprovalStatus,
    ExecutionLimits,
    FaultSpec,
    IdempotencyRecord,
    ScenarioDefinition,
    SessionState,
)
from agent_reliability_lab.policy.engine import PolicyOutcome, evaluate_protected_action
from agent_reliability_lab.simulator.determinism import stable_digest, stable_id, trace_digest
from agent_reliability_lab.simulator.state import business_state_hash, validate_world
from agent_reliability_lab.tools.base import ToolData, ToolExecutionError, ToolSpec
from agent_reliability_lab.tools.registry import build_registry


class _WriteArguments(Protocol):
    idempotency_key: str
    expected_revision: int
    approval_id: str | None


class Simulator:
    """Offline world engine whose complete behavior is replayable."""

    def __init__(
        self,
        scenario: ScenarioDefinition,
        *,
        principal_id: str,
        limits: ExecutionLimits,
        registry: dict[str, ToolSpec] | None = None,
    ) -> None:
        self._scenario = scenario.model_copy(deep=True)
        self._initial_world = scenario.world.model_copy(deep=True)
        self._registry = registry or build_registry()
        self._limits = limits
        self._principal_id = principal_id
        self._validate_scenario_contract()
        self.world = self._initial_world.model_copy(deep=True)
        self.session = SessionState(principal_id=principal_id)
        self._attempts: Counter[str] = Counter()
        validate_world(self.world)

    def _validate_scenario_contract(self) -> None:
        unknown = {fault.tool_name for fault in self._scenario.faults} - set(self._registry)
        if unknown:
            raise ValueError(f"faults reference unknown tools: {sorted(unknown)}")
        unknown_allowed = set(self._limits.allowed_tools) - set(self._registry)
        if unknown_allowed:
            raise ValueError(f"allowed_tools reference unknown tools: {sorted(unknown_allowed)}")

    def reset(self) -> None:
        """Restore the exact initial world and all deterministic counters."""

        self.world = self._initial_world.model_copy(deep=True)
        self.session = SessionState(principal_id=self._principal_id)
        self._attempts.clear()
        validate_world(self.world)

    @property
    def trace_hash(self) -> str:
        return trace_digest(self.session.trace)

    @property
    def state_hash(self) -> str:
        return business_state_hash(self.world)

    @property
    def attempt_counts(self) -> dict[str, int]:
        return dict(sorted(self._attempts.items()))

    def tool_schemas(self) -> list[ToolData]:
        return [self._registry[name].json_schema() for name in sorted(self._limits.allowed_tools)]

    def _fault_for(self, tool_name: str, attempt: int, phase: FaultPhase) -> FaultSpec | None:
        for fault in self._scenario.faults:
            if fault.tool_name == tool_name and fault.attempt == attempt and fault.phase is phase:
                return fault
        return None

    def _fault_error(self, fault: FaultSpec) -> ErrorDetail:
        mapping = {
            FaultKind.TIMEOUT: ("tool_timeout", "The tool timed out.", True),
            FaultKind.HTTP_429: ("tool_rate_limited", "The tool returned HTTP 429.", True),
            FaultKind.HTTP_500: ("tool_upstream_error", "The tool returned HTTP 500.", True),
            FaultKind.MALFORMED_RESPONSE: (
                "malformed_tool_response",
                "The tool returned a malformed response.",
                False,
            ),
            FaultKind.LOST_RESPONSE: (
                "response_lost_after_effect",
                "The effect committed, but its response was lost.",
                True,
            ),
        }
        code, message, retryable = mapping[fault.kind]
        return ErrorDetail(code=code, message=message, retryable=retryable)

    def _result(
        self,
        *,
        call_id: str,
        tool_name: str,
        status: ToolStatus,
        data: ToolData | None = None,
        error: ErrorDetail | None = None,
        committed: bool = False,
        replayed: bool = False,
        stale: bool = False,
        fault_id: str | None = None,
    ) -> ToolResult:
        return ToolResult(
            call_id=call_id,
            tool_name=tool_name,
            status=status,
            data=data,
            error=error,
            state_revision=self.world.revision,
            state_hash=self.state_hash,
            committed=committed,
            replayed=replayed,
            stale=stale,
            fault_id=fault_id,
        )

    def _finalize(
        self,
        *,
        result: ToolResult,
        effect: ToolEffect | None,
        request_digest: str,
        state_before_hash: str,
        policy_decision: PolicyDecision,
    ) -> ToolResult:
        trace = ToolTrace(
            sequence=self.session.call_sequence,
            call_id=result.call_id,
            tool_name=result.tool_name,
            effect=effect,
            status=result.status,
            request_digest=request_digest,
            state_before_hash=state_before_hash,
            state_after_hash=result.state_hash,
            state_revision=result.state_revision,
            policy_decision=policy_decision,
            error_code=result.error.code if result.error else None,
            committed=result.committed,
            replayed=result.replayed,
            stale=result.stale,
            fault_id=result.fault_id,
        )
        self.session.trace.append(trace)
        if effect is ToolEffect.READ and result.status is ToolStatus.OK:
            self.session.observation_call_ids.add(result.call_id)
        return result

    def _typed_error(
        self,
        *,
        call_id: str,
        tool_name: str,
        effect: ToolEffect | None,
        request_digest: str,
        state_before_hash: str,
        status: ToolStatus,
        code: str,
        message: str,
        retryable: bool = False,
        details: ToolData | None = None,
        policy_decision: PolicyDecision = PolicyDecision.NOT_APPLICABLE,
    ) -> ToolResult:
        result = self._result(
            call_id=call_id,
            tool_name=tool_name,
            status=status,
            error=ErrorDetail(
                code=code,
                message=message,
                retryable=retryable,
                details=details or {},
            ),
        )
        return self._finalize(
            result=result,
            effect=effect,
            request_digest=request_digest,
            state_before_hash=state_before_hash,
            policy_decision=policy_decision,
        )

    def call_tool(self, tool_name: str, arguments: dict[str, JsonValue]) -> ToolResult:
        """Execute one call; expected failures never cross this boundary as exceptions."""

        self.session.call_sequence += 1
        call_id = stable_id("call", self.session.call_sequence)
        state_before_hash = self.state_hash
        request_digest = stable_digest({"tool_name": tool_name, "arguments": arguments})

        spec = self._registry.get(tool_name)
        if spec is None:
            return self._typed_error(
                call_id=call_id,
                tool_name=tool_name,
                effect=None,
                request_digest=request_digest,
                state_before_hash=state_before_hash,
                status=ToolStatus.ERROR,
                code="unknown_tool",
                message=f"Unknown tool: {tool_name}",
            )
        if self.session.call_sequence > self._limits.max_tool_calls:
            return self._typed_error(
                call_id=call_id,
                tool_name=tool_name,
                effect=spec.effect,
                request_digest=request_digest,
                state_before_hash=state_before_hash,
                status=ToolStatus.ERROR,
                code="tool_budget_exceeded",
                message="The task tool-call budget is exhausted.",
            )
        if tool_name not in self._limits.allowed_tools:
            return self._typed_error(
                call_id=call_id,
                tool_name=tool_name,
                effect=spec.effect,
                request_digest=request_digest,
                state_before_hash=state_before_hash,
                status=ToolStatus.POLICY_DENIED,
                code="tool_not_allowed",
                message="This task does not allow the requested tool.",
                policy_decision=PolicyDecision.DENY,
            )

        try:
            parsed = spec.input_model.model_validate(arguments)
        except ValidationError as exc:
            validation_errors = json.loads(exc.json(include_url=False))
            details = {"validation_errors": cast(JsonValue, validation_errors)}
            return self._typed_error(
                call_id=call_id,
                tool_name=tool_name,
                effect=spec.effect,
                request_digest=request_digest,
                state_before_hash=state_before_hash,
                status=ToolStatus.ERROR,
                code="invalid_arguments",
                message="Tool arguments do not match the schema.",
                details=details,
            )

        intent: ActionIntent | None = None
        idempotency_scope: str | None = None
        policy = PolicyOutcome(
            decision=PolicyDecision.NOT_APPLICABLE,
            status=ToolStatus.OK,
        )
        if spec.effect is ToolEffect.WRITE:
            if spec.intent_factory is None:
                return self._typed_error(
                    call_id=call_id,
                    tool_name=tool_name,
                    effect=spec.effect,
                    request_digest=request_digest,
                    state_before_hash=state_before_hash,
                    status=ToolStatus.ERROR,
                    code="invalid_tool_definition",
                    message="Write tools require an intent factory.",
                )
            write_args = cast(_WriteArguments, parsed)
            idempotency_key = write_args.idempotency_key
            idempotency_scope = (
                f"{self.world.scenario_id}:{self._principal_id}:{tool_name}:{idempotency_key}"
            )
            existing = self.world.idempotency_ledger.get(idempotency_scope)
            if existing is not None:
                if existing.request_digest == request_digest:
                    result = self._result(
                        call_id=call_id,
                        tool_name=tool_name,
                        status=ToolStatus.OK,
                        data=existing.result_data,
                        replayed=True,
                    )
                    return self._finalize(
                        result=result,
                        effect=spec.effect,
                        request_digest=request_digest,
                        state_before_hash=state_before_hash,
                        policy_decision=PolicyDecision.ALLOW,
                    )
                return self._typed_error(
                    call_id=call_id,
                    tool_name=tool_name,
                    effect=spec.effect,
                    request_digest=request_digest,
                    state_before_hash=state_before_hash,
                    status=ToolStatus.CONFLICT,
                    code="idempotency_conflict",
                    message="The idempotency key is already bound to another request.",
                )
            try:
                intent = spec.intent_factory(self.world, parsed)
            except ToolExecutionError as exc:
                return self._typed_error(
                    call_id=call_id,
                    tool_name=tool_name,
                    effect=spec.effect,
                    request_digest=request_digest,
                    state_before_hash=state_before_hash,
                    status=exc.status,
                    code=exc.code,
                    message=exc.message,
                    retryable=exc.retryable,
                    details=exc.details,
                )

            expected_revision = write_args.expected_revision
            if expected_revision != self.world.revision:
                return self._typed_error(
                    call_id=call_id,
                    tool_name=tool_name,
                    effect=spec.effect,
                    request_digest=request_digest,
                    state_before_hash=state_before_hash,
                    status=ToolStatus.CONFLICT,
                    code="stale_revision",
                    message="expected_revision does not match the current world revision.",
                    details={"expected": expected_revision, "actual": self.world.revision},
                )
            if spec.protected:
                policy = evaluate_protected_action(
                    world=self.world,
                    intent=intent,
                    approval_id=write_args.approval_id,
                    forbidden_intents=self._limits.forbidden_intents,
                )
            elif intent.action in self._limits.forbidden_intents:
                policy = PolicyOutcome(
                    decision=PolicyDecision.DENY,
                    status=ToolStatus.POLICY_DENIED,
                    code="intent_forbidden",
                    message=f"The task forbids {intent.action}.",
                )
            else:
                policy = PolicyOutcome(decision=PolicyDecision.ALLOW, status=ToolStatus.OK)
            if policy.status is not ToolStatus.OK:
                return self._typed_error(
                    call_id=call_id,
                    tool_name=tool_name,
                    effect=spec.effect,
                    request_digest=request_digest,
                    state_before_hash=state_before_hash,
                    status=policy.status,
                    code=policy.code or "policy_denied",
                    message=policy.message or "The policy denied the action.",
                    policy_decision=policy.decision,
                )

        self._attempts[tool_name] += 1
        attempt = self._attempts[tool_name]
        before_fault = self._fault_for(tool_name, attempt, FaultPhase.BEFORE_EFFECT)
        if before_fault is not None:
            if before_fault.kind is FaultKind.STALE_RESPONSE:
                result = self._result(
                    call_id=call_id,
                    tool_name=tool_name,
                    status=ToolStatus.OK,
                    data=before_fault.payload,
                    stale=True,
                    fault_id=before_fault.fault_id,
                )
                return self._finalize(
                    result=result,
                    effect=spec.effect,
                    request_digest=request_digest,
                    state_before_hash=state_before_hash,
                    policy_decision=policy.decision,
                )
            result = self._result(
                call_id=call_id,
                tool_name=tool_name,
                status=ToolStatus.ERROR,
                error=self._fault_error(before_fault),
                fault_id=before_fault.fault_id,
            )
            return self._finalize(
                result=result,
                effect=spec.effect,
                request_digest=request_digest,
                state_before_hash=state_before_hash,
                policy_decision=policy.decision,
            )

        candidate = self.world.model_copy(deep=True)
        try:
            data = spec.handler(candidate, parsed, self.session)
            committed = False
            if spec.effect is ToolEffect.WRITE:
                assert intent is not None
                assert idempotency_scope is not None
                event_id = stable_id("event", candidate.next_event_sequence)
                candidate.revision += 1
                event = DomainEvent(
                    event_id=event_id,
                    sequence=candidate.next_event_sequence,
                    minute=candidate.now_minute,
                    event_type=f"{tool_name}_committed",
                    tool_name=tool_name,
                    intent_digest=intent.digest,
                    principal_id=self._principal_id,
                    policy_decision=policy.decision,
                    approval_id=policy.approval.approval_id if policy.approval else None,
                    idempotency_scope=idempotency_scope,
                    state_revision=candidate.revision,
                    payload=data,
                )
                candidate.next_event_sequence += 1
                candidate.events = (*candidate.events, event)
                if policy.approval is not None:
                    approval = candidate.approvals[policy.approval.approval_id]
                    approval.status = ApprovalStatus.CONSUMED
                    approval.consumed_event_id = event_id
                candidate.idempotency_ledger[idempotency_scope] = IdempotencyRecord(
                    scope=idempotency_scope,
                    tool_name=tool_name,
                    request_digest=request_digest,
                    intent_digest=intent.digest,
                    event_id=event_id,
                    result_data=data,
                    committed_revision=candidate.revision,
                )
                validate_world(candidate)
                self.world = candidate
                committed = True
            elif business_state_hash(candidate) != state_before_hash:
                raise RuntimeError("a non-write tool attempted to mutate business state")
        except ToolExecutionError as exc:
            return self._typed_error(
                call_id=call_id,
                tool_name=tool_name,
                effect=spec.effect,
                request_digest=request_digest,
                state_before_hash=state_before_hash,
                status=exc.status,
                code=exc.code,
                message=exc.message,
                retryable=exc.retryable,
                details=exc.details,
                policy_decision=policy.decision,
            )
        except Exception:
            return self._typed_error(
                call_id=call_id,
                tool_name=tool_name,
                effect=spec.effect,
                request_digest=request_digest,
                state_before_hash=state_before_hash,
                status=ToolStatus.ERROR,
                code="internal_execution_error",
                message="The tool failed without committing state.",
                policy_decision=policy.decision,
            )

        after_fault = self._fault_for(tool_name, attempt, FaultPhase.AFTER_EFFECT)
        if after_fault is not None:
            if after_fault.kind is FaultKind.STALE_RESPONSE:
                result = self._result(
                    call_id=call_id,
                    tool_name=tool_name,
                    status=ToolStatus.OK,
                    data=after_fault.payload,
                    committed=committed,
                    stale=True,
                    fault_id=after_fault.fault_id,
                )
            else:
                result = self._result(
                    call_id=call_id,
                    tool_name=tool_name,
                    status=ToolStatus.ERROR,
                    error=self._fault_error(after_fault),
                    committed=committed,
                    fault_id=after_fault.fault_id,
                )
        else:
            result = self._result(
                call_id=call_id,
                tool_name=tool_name,
                status=ToolStatus.OK,
                data=data,
                committed=committed,
            )
        return self._finalize(
            result=result,
            effect=spec.effect,
            request_digest=request_digest,
            state_before_hash=state_before_hash,
            policy_decision=policy.decision,
        )
