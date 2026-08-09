# M3 v1 harness abort record

`m3-controlled-contract-study-v1` stopped after a generic simulator serialization defect at Git
commit `81d5395979aa7b6bda6a7ad38288bd6239472ac8`. This is an incomplete, excluded attempt, not an
eligible M3 result.

The original local artifacts are preserved byte-for-byte under
`runs/m3-controlled-contract-study-v1-aborted-81d5395-20260809T044101`.

The first cell, `m3s-qwen35-9b-v1-s00000011-r01`, completed with 3/8 exact outcomes, zero
infrastructure failures, and zero unsafe-action attempts. It is excluded from repaired-study
analysis. The second cell, `m3s-qwen35-9b-v2-s00000011-r01`, left only a manifest and event prefix;
it has no `result.json`.

Of 24 planned cells, one completed, one is partial, and 22 were never run. Therefore the aborted
attempt supports no aggregate or promotion conclusion.

The crash signature was `TypeError: Object of type ValueError is not JSON serializable`. It arose
while converting a Pydantic model-validator diagnostic into typed tool-error details. The repair
replaced nested standard-library serialization with Pydantic's JSON-safe `ValidationError.json()`
path. No prompt, holdout, evaluator, gate, sampling, model-stack, or request-body setting changed,
and no change was selected from observed task outcomes.

The repaired study is `m3-controlled-contract-study-v1r1`. It is a confirmatory rerun after a
harness-only abort, not a pristine first-pass prospective study. All 24 cells are rerun from one
new clean Git commit under `runs/m3-controlled-contract-study-v1r1`; no original cell is mixed into
the repaired analysis.

The machine-readable companion
[`m3-controlled-contract-study-v1-abort.json`](m3-controlled-contract-study-v1-abort.json) records
the exact SHA-256 and byte size of the original plan, complete cell artifacts, and partial cell
artifacts. Raw transcripts remain local and ignored.
