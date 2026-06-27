# Copilot instructions

Read [`../AGENTS.md`](../AGENTS.md) first. It is the **single source of truth** for this repository's
orientation, terminology, target selection, and setup commands. This file is intentionally a thin
pointer — do not copy AGENTS.md content here, so the two cannot drift.

Never read, print, commit, or infer secrets from `.env` or other local environment files.

## User-facing skills

Use the matching prompt file when the user's request matches:

- **run-assert-eval** (`.github/prompts/run-assert-eval.prompt.md`): Run an end-to-end ASSERT evaluation from a plain-language requirement. Generates config, runs the pipeline, and summarizes scored results with cited failures. Reports `policy_violation` and `overrefusal` separately.

Equivalent guidance for other assistants lives in `.claude/skills/run-assert-eval/SKILL.md` (Claude Code)
and `.cursor/rules/assert.mdc` (Cursor). Keep all three aligned when you change the methodology.
