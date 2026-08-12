# audit-pr

## Purpose

Review a pull request for release-readiness across behavior naming, OpenTelemetry
/ OpenInference trace attribute compliance, dataset coverage, and first-run UX.

The review is evidence-first. It reports pass/fail by dimension with concrete file or diff evidence. It does not approve, merge, comment externally, or make changes.

## When to use

Use this skill when a PR changes any of these surfaces:

- `eval_config.yaml` or other eval spec examples
- `pipeline.systematize`, `pipeline.test_set`, `pipeline.inference`, or `pipeline.judge` configuration
- trace capture code, OpenInference instrumentation, or target execution wrappers
- generated or hand-authored test cases, example datasets, or docs that describe coverage
- install/setup commands, skills/prompts/rules, public examples, viewer startup,
  or any change that affects time to first useful result

## Inputs

- PR diff or local patch
- Changed config files and example files
- Any affected artifacts: `taxonomy.json`, `test_set.jsonl`, `inference_set.jsonl`, `scores.jsonl`, `metrics.json`
- Trace samples when the PR changes `target.trace` or instrumentation

## Dimensions audited

| Dimension | Pass criteria | Fail criteria |
|---|---|---|
| Behavior naming | `behavior.name` is stable, readable, customer-facing, and maps cleanly to `behavior.description`. Names avoid launch-phase labels, owner names, and internal shorthand. | Name is vague, overloaded, implementation-specific, or disconnected from the eval spec. |
| OpenInference attribute compliance | Captured spans include the OpenInference trace attributes needed for judge evidence, such as span kind, input/output values, model or tool identity, and run/session linkage when available. | Missing attributes prevent the judge from citing tool calls, routing, model calls, or execution context. |
| Dataset coverage | Test cases cover the behavior categories, important dimensions, edge cases, and expected failure modes without obvious duplicates. | Dataset is too narrow, duplicates cases, skips a behavior category, or cannot support the claimed scoring outcome. |
| First-run UX | From a fresh worktree, setup has one pinned path, a five-case smoke result appears before the full-run commitment, terminal text/JSON both work, and the user is asked before a long run. | Setup assumes maintainer state or another checkout, command success targets the wrong repo, JSON/viewer fails, private examples leak, or the first useful result is gated behind a multi-hour run. |

## Output template

| Dimension | Pass / fail | Evidence |
|---|---|---|
| Behavior naming | Pass/Fail | `<file>:<line>` plus one sentence. |
| OpenInference attribute compliance | Pass/Fail | `<file>:<line>` or trace sample field plus one sentence. |
| Dataset coverage | Pass/Fail | `<file>:<line>` or artifact row plus one sentence. |
| First-run UX | Pass/Fail | Timed fresh-worktree command plus one sentence. |

**Summary:** `<one-line overall finding with the highest-severity blocker first>`

## Example output snippet

| Dimension | Pass / fail | Evidence |
|---|---|---|
| Behavior naming | Pass | `examples/refund_bot/eval_config.yaml:4` uses `refund-policy-compliance`, which maps directly to the eval spec. |
| OpenInference attribute compliance | Fail | `examples/refund_bot/target.py:42` records tool output but omits `openinference.span.kind`, so tool calls are not distinguishable in judge evidence. |
| Dataset coverage | Pass | `examples/refund_bot/test_set.jsonl` includes policy-compliant, ambiguous, and adversarial refund requests. |

**Summary:** Fails on trace evidence: add the missing OpenInference span kind before relying on tool-call scoring.
