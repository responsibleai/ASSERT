# Verification

This directory tracks the spec-vs-implementation matrix for adaptive-eval. It answers one question per row: does the system actually do what we say it does?

## What lives here

- [`matrix.md`](matrix.md) - human-readable table, one row per claim
- [`matrix.json`](matrix.json) - same data, machine-readable, consumed by the regression harness
- This README - schema and how to add a row

## Two row types

The matrix mixes two distinct kinds of claim. Both are legitimate; we keep them in the same matrix so a single regression run can cover both spines.

- **Framework claims** (`AE-*` IDs). Claims about adaptive-eval itself, sourced from `docs/adaptive-eval-spec.md`. Example: "Stage 4 Judge produces `scores.jsonl` with per-node reasoning." Verified by running the pipeline against a fixture and asserting artifact shape and contents.
- **Scenario coverage** (`TP-*`, future scenario prefixes). Claims that the eval pipeline catches the failure modes a reference scenario is built around. Example for the travel planner: when the agent hallucinates flight numbers, the judge flags it as a quality failure. Verified by running the scenario end-to-end and confirming labeled failures are caught.

When you add a row, pick the type first. Framework rows describe behavior of the engine; scenario rows describe whether the engine catches what it should catch.

## Schema

Each row in the matrix represents one claim. Every row has the same fields:

| Field | Type | Description |
|---|---|---|
| `spec_id` | string | Stable identifier. Owned by the spec author for framework rows, by the scenario maintainer for scenario rows. |
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
2. Add the row to both `matrix.md` and `matrix.json`. Keep them in sync.
3. Use a stable `spec_id` from the source. If the source doesn't have stable IDs yet, use a placeholder (`AE-*` for framework, `TP-*` for the travel-planner scenario, etc.) and re-key when canonical IDs land.
4. Default `owner` to whoever is resolving the row, not the spec author.
5. Open a PR. Verification reviews matrix changes.

## Cadence

The matrix is reviewed weekly with the spec author. Rows in `gap-*` states get a build / cut / push-back decision; rows in `verified` get spot-checked against the latest regression run.

## Status

This is **v0**. Framework rows seeded from `docs/adaptive-eval-spec.md` (pipeline stages, CLI surface, target shapes). Scenario rows seeded from `tests/regression/risks/travel_planner_*.md`. All `spec_id` values are placeholders until the canonical schemes land.
