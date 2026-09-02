# Copilot instructions

Read [`../AGENTS.md`](../AGENTS.md) first. It is the **single source of truth** for this repository's
orientation, terminology, target selection, and setup commands. This file is intentionally a thin
pointer — do not copy AGENTS.md content here, so the two cannot drift.

Never read, print, commit, or infer secrets from `.env` or other local environment files.

## User-facing skills

Use the matching prompt file when the user's request matches:

- **run-assert-eval** (`.github/prompts/run-assert-eval.prompt.md`): Run an end-to-end ASSERT evaluation against a described risk. Risks come from Clarity (recommended — drives the Clarity MCP tools (`run_clarity`) in-IDE to surface failure modes the user hasn't considered) or directly from the user as prose, a PRD, design doc, threat model, or red-team finding; Clarity is never required. Then follows `workflows/measure-clarity-failures.md` — human triage, splits the selected risks into one atomic config per behavior, runs the pipeline, and summarizes scored results with cited failures. Config generation is owned by `workflows/research-eval-dimensions.md`, which runs downstream of both risk sources: given an already-named risk, it reviews **how that risk has been evaluated** in the literature and converts the findings into the test-set design — every behavior category, stratify dimension, and judge dimension is researched against retrieved primary sources under a ≥2-independent-source gate, `N` complete generation passes are run and semantically deduplicated, the dimension set is blocked on explicit user approval, and the result is written as a cited `examples/<domain>/<risk>/eval_config.yaml`. Reports `policy_violation` and `overrefusal` separately. To fix and *prove* a failure, `workflows/govern-and-remeasure.md` generates an ACS policy from the findings (`assert-ai acs generate`) and re-runs the same eval against the governed agent to measure the failure-rate delta.

Equivalent guidance for other assistants lives in `.claude/skills/run-assert-eval/SKILL.md` (Claude Code)
and `.cursor/rules/assert.mdc` (Cursor). Keep all three aligned when you change the methodology.
