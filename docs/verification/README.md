# Verification

This directory tracks the spec-vs-implementation matrix for adaptive-eval. It answers one question per row: does the system actually do what we say it does?

## What lives here

- [`matrix.md`](matrix.md) - human-readable table, one row per claim
- [`matrix.json`](matrix.json) - same data, machine-readable, consumed by the regression harness
- [`verify.py`](verify.py) - dispatch harness that runs the registered verifiers against a suite directory and prints a PASS / FAIL / NOT_IMPLEMENTED summary
- This README - schema, ID convention, and how to add a row

## Two row types

The matrix mixes two distinct kinds of claim. Both are legitimate; we keep them in the same matrix so a single regression run can cover both spines.

- **Framework claims** (`AE-*` IDs). Claims about adaptive-eval itself, sourced from the implementation surface in `p2m/` (pipeline stages, CLI commands, target shapes). Example: "Stage 4 Judge produces `scores.jsonl` with per-node reasoning." Verified by running the pipeline against a fixture and asserting artifact shape and contents, or by scanning the source tree for the expected code surface.
- **Scenario coverage** (per-scenario prefix; e.g. `P2M-AGENT-*`, `P2M-FW-*`, `P2M-SCALE-*`, `TP-*`). Claims that the eval pipeline catches the failure modes a reference scenario is built around, or that it runs end-to-end across the framework / scale matrix we ship demos for. Verified by running the scenario end-to-end and confirming labeled failures are caught or that the run produced the expected artifacts.

When you add a row, pick the type first. Framework rows describe behavior of the engine; scenario rows describe whether the engine catches what it should catch (or runs against the targets we promise to support).

## ID convention

IDs are stable identifiers for verification slots. They are intentionally **not** anchored to spec-doc section numbers - section numbers churn, subsystems don't. When you rename a row's claim text, keep the same ID. The ID identifies the slot, not the wording.

**Framework rows** - `AE-{SUBSYSTEM}-{IDENTIFIER}`

- `AE-PIPE-S{1..5}` - pipeline stage claims (S1=Policy, S2=Seeds, S3=Rollout, S4=Judge, S5=Metrics)
- `AE-PIPE-{TOPIC}` - non-stage pipeline claims (e.g. `AE-PIPE-ART` for artifact layout)
- `AE-CLI-{COMMAND}` - CLI surface (e.g. `AE-CLI-RUN`, `AE-CLI-FORCE`, `AE-CLI-RESULTS`)
- `AE-TGT-{SHAPE}` - target shapes (e.g. `AE-TGT-MODEL`, `AE-TGT-CALLABLE`, `AE-TGT-HTTP`, `AE-TGT-OTEL-PHX`, `AE-TGT-OTEL-CUSTOM`)
- `AE-X-{TOPIC}` - cross-cutting concerns (e.g. `AE-X-LITELLM`)

**Scenario rows** - `{SCENARIO}-{CATEGORY}-{IDENTIFIER}`

- `{SCENARIO}` - short scenario prefix. `P2M` is the in-tree P0 set scoped by Chang on May 7 (two agents probed across framework breadth and scalability). `TP` is the legacy travel-planner-only prefix. Future scenarios pick their own 2-3 letter prefix and register it here.
- `{CATEGORY}` - subdivision of the scenario set:
  - For `P2M`: `AGENT` (per-agent behavioral correctness), `FW` (framework / endpoint breadth), `SCALE` (seed-count scalability).
  - For per-agent quality/safety scenarios (e.g. `TP-Q-*`, `TP-S-*`): single letter, `Q` = quality, `S` = safety. Add letters as new categories appear.
- `{IDENTIFIER}` - either a short name (e.g. `HEALTH`, `LANGGRAPH`, `100`) or a zero-padded sequential number (e.g. `001`). Numbers are never recycled - deleted rows stay deleted; new rows get the next number.

**Stability rules:**

- IDs survive row deletion (`gap-cut`) - never recycled to new rows.
- IDs are intentionally not anchored to spec-doc section numbers. `docs/adaptive-eval-spec.md` does not exist yet, and even when it lands, section numbers will renumber more often than subsystem boundaries shift.
- When you rename a row's claim text, keep the same ID.

## Schema

Each row in the matrix represents one claim. Every row has the same fields:

| Field | Type | Description |
|---|---|---|
| `spec_id` | string | Stable identifier following the ID convention above. |
| `claim` | string | One-line description of what the system claims to do (framework) or catch (scenario). |
| `status` | enum | `verified`, `gap-build`, `gap-cut`, or `gap-pushback`. See below. |
| `evidence` | string \| null | Link to the regression run, PR, or decision thread that backs the status. |
| `owner` | string | GitHub handle of the person on the hook to resolve this row. |
| `target_date` | YYYY-MM-DD \| null | When this row resolves to `verified` or `gap-cut`. |

## Status values

- **`verified`** - regression confirms the claim. Evidence link points to a passing run.
- **`gap-build`** - claim is real, system doesn't do it yet, we're building. Evidence points to the in-flight PR or tracking issue.
- **`gap-cut`** - claim is removed from scope. Evidence points to the decision thread.
- **`gap-pushback`** - claim is wrong; spec needs an update before we can verify. Evidence points to the spec-change discussion.

## How to add a row

1. Decide whether it is a framework row or a scenario row.
2. Pick a `spec_id` following the ID convention. For new scenario prefixes, register the prefix in this README.
3. Add the row to both `matrix.md` and `matrix.json`. Keep them in sync.
4. If the verification is automatable, add the verifier to `verify.py` under the matching `@verifier(spec_id)` decorator. If not, leave the row as `gap-build` with `evidence: null` until a verifier lands.
5. Default `owner` to whoever is resolving the row, not the spec author.
6. Open a PR. Verification reviews matrix changes.

## Cadence

The matrix is reviewed weekly with the spec author. Rows in `gap-*` states get a build / cut / push-back decision; rows in `verified` get spot-checked against the latest regression run.
