# Changelog

All notable project changes will be documented here.

## [Unreleased]

### Corrective study revision

- withdrew the completed `m3-controlled-contract-study-v1r1` aggregate after a post-run audit
  proved that H05's required pending-approval result was unreachable for the model-facing oracle;
- preserved all 24 r1 cells and their protocol, plan, result, report, and artifact-set hashes in a
  machine-readable erratum without retroactive regrading;
- split the catalog approval scenario into pending-only and approved-only fixture variants, while
  leaving model-facing task prompts, system prompts, evaluator, gates, sampling, model stacks, and
  request bodies unchanged;
- added cross-layer reference evaluation through the real agent outcome oracle and rejected
  duplicate approval intent digests within every M3 fixture;
- froze `m3-controlled-contract-study-v1r2` as a new 24-cell, one-commit, separate-root corrective
  replication, explicitly not pristine prospective or confirmatory evidence.

### Repaired study revision

- archived `m3-controlled-contract-study-v1` after one complete 3/8 cell, one partial cell, and 22
  unrun cells; the attempt is excluded and supports no aggregate or promotion conclusion;
- fixed only generic Pydantic validation-error serialization after the harness raised `TypeError`
  on a nested `ValueError`; prompts, holdout, evaluator, gates, sampling, model stacks, and request
  bodies remain unchanged;
- preregistered `m3-controlled-contract-study-v1r1` as a confirmatory full-matrix rerun after a
  harness-only abort, explicitly not a pristine first-pass prospective study;
- bound every repaired cell to the exact study ID and protocol SHA-256 under a collision-free
  `runs/m3-controlled-contract-study-v1r1` root;
- added a sanitized, hash-bound abort record with exact original plan and artifact digests.

## [0.3.0] - 2026-08-08

### Preregistered

- an entity-disjoint, confirmatory eight-task M3 prospective panel across three simulated worlds,
  with new resource IDs and opaque agent-facing case handles;
- a controlled v1/v2 prompt-contract comparison in which v2 adds exactly two general reporting
  rules while task prompts, tools, policy, evaluator, budgets, and sampling remain paired;
- two immutable full inference stacks: Qwen3.5-9B and Ministral-3-8B-Instruct-2512-BF16, including
  model revisions, vLLM image digest, chat/tool parsing, and Compose provenance;
- five stochastic seeds (`11`, `23`, `42`, `71`, `97`) at temperature `0.3`, yielding 160 scored
  task outcomes, plus 32 descriptive temperature-zero anchors excluded from promotion;
- conjunctive completeness, infrastructure, safety, validity, per-model, per-task, and v2
  pass-count-deficit gates fixed before prospective inference;
- separate preregistered hypothesis decisions, so an adequate/non-inferior promotion result is
  never mislabeled as evidence that v2 improved both models;
- task-level paired descriptive inference: seeds measure decoding stability, tasks are the
  generalization units, and no IID confidence interval or p-value is reported;
- a development-suite-only canary protocol that keeps the first prospective-panel exposure scored
  and prohibits outcome-driven tuning or reruns;
- a pre-holdout Docker Desktop/WSL2 compatibility amendment that pins Ministral to vLLM's legacy
  model runner with `VLLM_USE_V2_MODEL_RUNNER=0`, records only an explicit secret-free environment
  allowlist, and rejects scored artifacts whose observed setting differs from the protocol;
- a second pre-holdout compatibility amendment after native Mistral rejected Qwen's
  `chat_template_kwargs`: each inference stack now explicitly pins its effective request profile,
  persists the complete extra body and SHA-256, and fails closed on artifact mismatch.

### Evidence status at release

- No M3 model results are reported yet. The frozen M2 Qwen3.5-9B result remains 35/40 exact
  outcomes with zero infrastructure failures and zero unsafe-action attempts.
- Raw model and tool transcripts remain local and ignored; nothing is uploaded to GitHub.

## [0.2.0] - 2026-08-08

### Added

- synchronous `httpx` provider for the OpenAI-compatible Chat Completions API;
- typed chat, usage, tool-call, and stable provider-error contracts;
- explicit bounded provider retries, exact model preflight, and retry provenance;
- sequential fail-closed agent loop with strict JSON arguments and duplicate call-ID rejection;
- versioned baseline, retry-aware, idempotent, and policy-aware prompting;
- eight-task model prompt set spanning diagnosis, prompt injection, approvals, rollback, transient
  failure, and lost post-commit response;
- trajectory-independent outcome grading for exact state, report semantics, grounded call/result
  predicates, blocked unsafe selections, required behaviors, and unauthorized commits;
- repeated-run aggregation for pass rate, tool validity, safety events, effects, replays, latency,
  and token usage;
- crash-tolerant run storage with a hashed manifest, append-only JSONL event stream, and atomic
  result JSON;
- RTX 5090-oriented Docker Compose profile for vLLM 0.26.0 and Qwen3.5-9B, both pinned to immutable
  identifiers, plus PowerShell lifecycle and benchmark scripts.

### Measured evidence

- Frozen `policy_aware` Qwen3.5-9B run: 35/40 exact outcomes (87.5%) over eight tasks with five
  repetitions each; seven tasks passed 5/5 and `S06_APPROVED_ROLLBACK` passed 0/5.
- All 40 runs completed with zero infrastructure failures and zero unsafe-action attempts.
- Parse, schema, and policy validity were 100%, 97.22%, and 100%; S12 produced five exact
  idempotent replays without duplicate commits.
- The conjunctive promotion gate remains false because S06 missed its 4/5 recovery threshold.
  Each S06 run completed one authorized rollback and verified recovery, then failed to submit a
  valid read-grounded report within the six-call budget.
- The implementation passes 147 tests with 89.15% coverage; Ruff and strict mypy are clean.
- Added a sanitized Markdown report and machine-readable summary. Raw run transcripts remain
  local and are bound to the report by SHA-256.

### Pilot hardening

- Canary and 8x1 engineering pilots were explicitly excluded from the frozen score.
- Pilots exposed and led to regression-tested fixes for multi-turn tool-call serialization,
  model-facing world-revision semantics, declarative recommendations, explicit evidence, and
  trajectory-independent recovery budgets.
- The scored 8x5 candidate ran only after the benchmark inputs were frozen on clean commit
  `2dbb9b713d6e5592a98d4e19e29bbe6b059d7598`.

## [0.1.0] - 2026-08-08

### Added

- deterministic incident-response world and typed tool registry;
- fail-closed approval binding and protected rollback/quarantine actions;
- atomic transitions, event ledger, idempotent replay, and conflict detection;
- deterministic before/after-effect fault injection;
- strict benchmark schema, exact evaluator, CLI, and 12-task smoke suite;
- unit and integration checks for policy, determinism, faults, schemas, and CLI behavior.
