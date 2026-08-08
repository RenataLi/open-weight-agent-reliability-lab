# Architecture

## Design objective

The simulator must answer a stronger question than “did the agent produce plausible text?”:

> Did the agent reach the exact valid world state, through allowed actions, while recovering from
> deterministic failures without duplicating side effects?

The core therefore separates production state, session trace, and benchmark expectations.

## Execution boundary

`Simulator.call_tool()` is the only agent-facing execution boundary. Expected errors are returned
as typed results rather than raised through that boundary.

For each call it:

1. assigns a deterministic call ID;
2. checks the task tool budget and allowlist;
3. validates arguments with the registered Pydantic schema;
4. checks an existing idempotency record before rebuilding a state-dependent intent;
5. builds the canonical action intent for a new write;
6. checks optimistic revision and fail-closed policy;
7. applies a scheduled `before_effect` fault, if any;
8. runs the handler against a deep copy of world state;
9. validates cross-entity and security invariants;
10. records the event, approval consumption, and idempotency result atomically;
11. applies a scheduled `after_effect` response fault, if any;
12. appends a deterministic session trace.

Only step 10 replaces the live world.

## State layers

### Business state

Services, deployments, data sources, and incidents. The public `state_hash` includes only these
entities, so reads and technical bookkeeping do not appear as production changes.

### Control state

Approvals, committed events, revision, and the idempotency ledger. This state is part of the full
world model and its invariants, but excluded from the business-state hash.

### Session state

Tool trace, evidence call IDs, and the submitted incident report. Sessions never grant approval.

## Approval binding

Protected actions are converted to an `ActionIntent`. For rollback, the intent contains:

```json
{
  "action": "rollback_deployment",
  "resource_id": "ranker-prod",
  "arguments": {
    "service_id": "ranker-prod",
    "from_deployment_id": "deploy-002",
    "target_deployment_id": "deploy-001"
  }
}
```

Approval compares the SHA-256 digest of this exact structure. Permission for another service,
target, or resource snapshot cannot be substituted.

`now_minute == expires_at_minute` is expired. The boundary is deliberately fail-closed.

## Idempotency ordering

The engine checks the `(scenario, principal, tool, key)` ledger before rebuilding a
state-dependent intent. This is required after a successful rollback: the active deployment has
already changed, but a byte-for-byte equivalent network retry must still receive the stored
result. Reusing the key with any different canonical request returns `idempotency_conflict`.

## Fault phases

- `before_effect`: timeout, 429, 500, malformed response, or a predetermined stale response.
  Production state and the idempotency ledger remain unchanged.
- `after_effect`: the handler and commit have completed, but the response may be lost. A retry
  must recover the committed result from the idempotency ledger.

Policy-denied calls do not consume a fault attempt.

## Current trust boundaries

- Scenario fixtures and external approval decisions are trusted benchmark inputs.
- Agent arguments, retrieved runbooks, and tool outputs are untrusted.
- No real deployment, credential, shell command, or remote API is reachable from the simulator.
