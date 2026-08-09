# M3 study v1r1 H05 oracle erratum

Study `m3-controlled-contract-study-v1r1` completed all 24 cells on clean commit
`5646280dde25bd89330ef294252b169f9bb44635`, but its H05 outcome oracle was
unreachable. The r1 aggregate, hypothesis, and promotion conclusions are therefore withdrawn.
The raw r1 root remains immutable at `runs/m3-controlled-contract-study-v1r1`.

## Defect

`M3H05_QUARANTINE_APPROVAL_PENDING` required `request_approval` to return
`permit-82-pending` with status `pending`. Its shared fixture contained both that approval and
`permit-82-good` with status `approved`, bound to the same intent digest
`c929eb1241c4ac1b968226307673e0c7266f2692c6b164b637f2bff396e58418`.

The handler deterministically sorts matching approvals by ID and returns the first match.
`permit-82-good` sorts first, so the required pending result could never be produced. The legacy
reference evaluator still reported H05 as passing because it did not compare required tool-result
bodies. Replaying the same reference trajectory through the actual agent outcome evaluator fails
the `required_calls` check.

All 24 r1 H05 runs submitted their intended grounded report and failed only `required_calls`:
20 were stochastic study outcomes and four were descriptive greedy anchors. Every
`request_approval` call returned `permit-82-good` / `approved`. These outcomes cannot be
retroactively regraded because the model observed the wrong external-decision state.

## Correction and replication

The catalog scenario is split into an H05 pending-only fixture and an approved-only fixture used by
H08. Agent-oracle reachability and unique approval-intent-digest regressions cover all eight tasks.
Model-facing prompts, system prompts, evaluator semantics, promotion gates, sampling, pinned model
stacks, and request bodies are unchanged.

The complete 24-cell matrix must be rerun under
`m3-controlled-contract-study-v1r2` from one new clean commit into
`runs/m3-controlled-contract-study-v1r2`. No r1 cell may be mixed into r2. This is a fixed-panel
corrective replication after outcome exposure, not pristine, prospective, or confirmatory
evidence.

## Frozen r1 provenance

- Protocol SHA-256: `1d92852c8149a19c4b657bd3f59e82370f1612b752bfaaed1cf104ca102f731a`
- Artifact-set SHA-256: `f6c966fcaabde7b12e50b36b8b69fa17d7fef8d457ad5a8b561ac9774c9c23d7`
- Study plan SHA-256: `943737161ed7d068884fd02e10fe0e10c00cd6522b9ba776060c2d6608751891`
- Study result SHA-256: `5cb1ac330ce178877e355df068be7c325b6596a3ee84582f89f49840b869782f`
- Rendered report SHA-256: `1ece6e7a390ed218fba1e5fbcb872409a6b31f08fcc94f17dedd780801a8cd66`
- Invalidated r1 suite SHA-256: `3659bb8bfcae05ae7215cb4bc8bae8e6b1b225a9ed0f17c85185ce6c4de19a99`
- Corrected r2 suite SHA-256: `b5403fb934c4ca70cbc1afbb3bff08729a88a7a261fadff8a0a663d670662d7b`
