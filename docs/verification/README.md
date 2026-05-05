# Verification

This directory tracks the spec-vs-implementation matrix for adaptive-eval. It answers one question per row: does the system actually do what we say it does?

## What lives here

- [`matrix.md`](matrix.md) - human-readable table, one row per claim
- [`matrix.json`](matrix.json) - same data, machine-readable, consumed by the regression harness
- This README - schema and how to add a row

## Schema

Each row in the matrix represents one claim the system makes about its behavior. Every claim has the same fields:

| Field | Type | Description |
|---|---|---|
| `spec_id` | string | Stable identifier. Owned by the spec author. |
| `claim` | string | One-line description of what the system claims to do. |
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

1. Add the row to both `matrix.md` and `matrix.json`. Keep them in sync.
2. Use a stable `spec_id` from the source spec. If the spec doesn't have stable IDs yet, use a placeholder like `TP-Q-001` and re-key when the canonical IDs land.
3. Default `owner` to whoever is resolving the row, not the spec author.
4. Open a PR. Verification reviews matrix changes.

## Cadence

The matrix is reviewed weekly with the spec author. Rows in `gap-*` states get a build / cut / push-back decision; rows in `verified` get spot-checked against the latest regression run.

## Status

This is **v0**. Rows are seeded from the existing `tests/regression/risks/travel_planner_*.md` failure-mode lists, reversed into capability claims. Spec IDs are placeholders until the canonical scheme is locked.
