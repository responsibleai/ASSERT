# ASSERT Autonomous Agent System

ASSERT uses a small autonomous-agent system as operator tooling for repository maintenance. It is not user-facing product behavior; it helps the operator monitor repo health, docs UX, and public feedback while keeping all external writes under explicit human control.

## What lives here

- [Main system definition](../../AGENTS.md) — see the `Autonomous agent system (operator-only)` section, including the `Default state: VACATION MODE` rules and narrow write exceptions for the dev-maintainer agent.
- [Agent specs](../../.github/agents/) — definitions for dev-maintainer, designer, feedback, pm, and comms agents.
- [Skill specs](../../.github/skills/) — seven reusable skill definitions used by the agents.
- [Public-safe inboxes](inbox/) — observation templates for dev-maintainer, designer, and feedback outputs.

## Public-safe inboxes

The public inboxes are header-only templates until activation:

- `inbox/dev-inbox.md` — PR and issue audit findings.
- `inbox/designer-inbox.md` — docs-site and sample UX findings.
- `inbox/feedback-inbox.md` — anonymized feedback signals from transcripts and support threads.

Each inbox uses placeholder rows only. Do not add real entries unless Chang explicitly activates the corresponding agent and approves the destination.

## VACATION MODE

VACATION MODE is the default state.

In this state:

- Agents observe only.
- No external writes are allowed.
- No PRs are auto-opened.
- No issue or PR comments are auto-posted.
- No support, social, or comms replies are sent.

Activation requires explicit operator action by Chang. Until then, the inbox files remain empty templates.

## Sensitive agents

The pm and comms agents can produce sensitive operator-side outputs. Their outputs are stored in the operator-side internal workspace, not in this public docs area.

Public-facing ASSERT docs should keep using customer-safe terminology: behavior, dataset, test cases, OpenTelemetry, OpenInference, and spec-driven scoring.
