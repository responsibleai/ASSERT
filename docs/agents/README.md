# ASSERT Autonomous Agent System

ASSERT uses a small autonomous-agent system as operator tooling for repository maintenance. It is not user-facing product behavior; it helps the operator monitor repo health, docs UX, and public feedback while keeping all external writes under explicit human control.

## What lives here

- [Main system definition](../../AGENTS.md) — see the `Autonomous agent system (operator-only)` section, including the `Default state: VACATION MODE` rules and narrow write exceptions for the dev-maintainer agent.
- [Agent specs](../../.github/agents/) — definitions for dev-maintainer, designer, feedback, pm, and comms agents.
- [Skill specs](../../.github/skills/) — seven reusable skill definitions used by the agents.
- [Public-safe inboxes](inbox/) — observation templates for dev-maintainer, designer, and feedback outputs.

## Public-safe inboxes

The public inboxes ship as header-only templates:

- `inbox/dev-inbox.md` — PR and issue audit findings. **The dev-maintainer agent's two narrow write exceptions are active by default**, so this inbox begins receiving observation rows and audit summaries as soon as the dev-maintainer's recurring loop runs post-merge.
- `inbox/designer-inbox.md` — docs-site and sample UX findings. Empty until the designer agent is activated.
- `inbox/feedback-inbox.md` — anonymized feedback signals from transcripts and support threads. Empty until the feedback agent is activated.

Designer and feedback inboxes use placeholder rows only. Do not add real entries to those two unless Chang explicitly activates the corresponding agent and approves the destination.

## VACATION MODE

VACATION MODE is the default state.

In this state:

- Agents observe only, **with two narrow exceptions held by the dev-maintainer agent** (see [`AGENTS.md`](../../AGENTS.md) §"Narrow write exceptions"):
  1. Audit-only PR comments (technical observations; never an approving or request-changes review).
  2. Reviewer requests on PRs unassigned past the 24h escalation window (assigns existing CODEOWNERS only).
- No PRs are auto-opened.
- No approvals, no merges, no label changes, no issue closures.
- No issue comments are auto-posted. Only dev-maintainer audit comments on existing PRs are permitted.
- No support, social, or comms replies are sent.

The four other agents (designer, feedback, pm, comms) have **no** write exceptions in vacation mode. Activating broader writes for any agent — including new write capabilities for the dev-maintainer — requires explicit operator action by Chang per the activation procedure in `AGENTS.md`.

## Sensitive agents

The pm and comms agents can produce sensitive operator-side outputs. Their outputs are stored in the operator-side internal workspace, not in this public docs area.

Public-facing ASSERT docs should keep using customer-safe terminology: behavior, dataset, test cases, OpenTelemetry, OpenInference, and spec-driven scoring.
