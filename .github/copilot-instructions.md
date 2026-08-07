# Copilot instructions

Read [`../AGENTS.md`](../AGENTS.md) first. It is the **single source of truth** for this repository's
orientation, terminology, target selection, and setup commands. This file is intentionally a thin
pointer — do not copy AGENTS.md content here, so the two cannot drift.

Never read, print, commit, or infer secrets from `.env` or other local environment files.

## User-facing skills

Use the matching prompt file when the user's request matches:

- **run-assert-eval** (`.github/prompts/run-assert-eval.prompt.md`): Run an end-to-end ASSERT evaluation whose risks are discovered with Clarity. Drives the Clarity MCP tools (`run_clarity`) in-IDE to surface risks, then follows `workflows/measure-clarity-failures.md` — human triage, splits the selected risks into one atomic config per behavior, runs the pipeline, and summarizes scored results with cited failures. Reports `policy_violation` and `overrefusal` separately. To fix and *prove* a failure, `workflows/govern-and-remeasure.md` generates an ACS policy from the findings (`assert-ai acs generate`) and re-runs the same eval against the governed agent to measure the failure-rate delta.

Equivalent guidance for other assistants lives in `.claude/skills/run-assert-eval/SKILL.md` (Claude Code)
and `.cursor/rules/assert.mdc` (Cursor). Keep all three aligned when you change the methodology.
