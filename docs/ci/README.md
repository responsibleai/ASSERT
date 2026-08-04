# CI safety gate

Use [`responsibleai/assert-ai-action`](https://github.com/responsibleai/assert-ai-action) to run ASSERT in pull requests and fail on safety regressions.

## Setup

Install the skills into your coding agent — works with Cursor, Claude Code, Copilot, Gemini CLI, Windsurf, Codex, and [40+ others](https://github.com/vercel-labs/skills#supported-agents):

```bash
npx skills add responsibleai/ASSERT --skill run-assert-eval --yes
npx skills add responsibleai/assert-ai-action --skill wire-assert-ci --yes
```

Two commands, because the bundle spans two repositories on purpose. `wire-assert-ci` wires CI and delegates every live evaluation to `run-assert-eval`, which is owned here in ASSERT. Installing it from here rather than copying it into the action repo means it cannot drift out of sync.

Run them separately: `skills add` accepts one package per invocation and silently ignores extras while still exiting 0, so a combined command looks successful and installs half the bundle.

Then: *"Use the `wire-assert-ci` skill to add an ASSERT safety gate to this repo."*

Without Node, paste the bootstrap URL instead and the agent fetches the skills itself:

```text
read https://raw.githubusercontent.com/responsibleai/assert-ai-action/main/ONBOARD.md
```

## What the agent does

Scans the repo, picks the highest-fidelity way to reach your agent (auto-traced → bring-your-own-trace → callable → HTTP endpoint → prompt-agent), drafts an eval spec from your own README and prompts, asks you to confirm or replace it, splits it **one behavior per YAML**, runs a baseline, and opens the gate PR.

## Notes

Generated workflows call `responsibleai/assert-ai-action@v1`. Keep provider credentials in CI secrets and reference environment variable names only — there is no shared endpoint, so you supply your own model credentials.

The gate blocks on **evidence of harm**: a statistically significant regression fails the build. A missing baseline, a changed test set, or an inconclusive result does not block — see the action's README for the full verdict table and its security model.
