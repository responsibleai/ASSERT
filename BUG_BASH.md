# ASSERT bug bash — July 30, 2026

Use one track. Do not attempt both during the session.

## Track 1 — Clarity → ASSERT evaluation skill

This track starts from risks discovered by Clarity and turns selected failures
into separate ASSERT eval configurations and measured results.

Start here:

- [Skill overview](.claude/skills/run-assert-eval/README.md)
- [One-time MCP and environment setup](.claude/skills/run-assert-eval/SETUP-CHECKLIST.md)
- [Canonical skill instructions](.claude/skills/run-assert-eval/SKILL.md)

Prerequisites: an IDE with MCP tool support, a configured Clarity MCP server, an
LLM provider, and the ASSERT checkout installed. If Clarity is not already
available in the IDE, report setup friction rather than spending the whole
session hiding it.

## Track 2 — sandboxed action mediation

This track tests a configured agent inside ASSERT's stock Docker sandbox. It
covers first-time setup and evidence, argument-specific mocks, policy boundaries,
and disposable-state or failure handling.

Start here:

- [Action-mediation bug-bash guide](examples/sandbox_action_mediation/BUG_BASH.md)
- [Product and setup overview](examples/sandbox_action_mediation/README.md)

Prerequisites: Docker Desktop or Docker Engine, Python, and an editable install of
this checkout. No model credentials are required for the shared action-mediation
baseline.

## Common issue format

Use a title like:

```text
[Bug Bash][Evaluation Skill] Short description
[Bug Bash][Action Mediation] Short description
```

Include the branch and commit, operating system, exact steps, expected and actual
behavior, and a minimal artifact or screenshot when useful. Confusing setup,
unclear evidence, and unexpectedly slow steps count as findings even when the
command eventually succeeds.
