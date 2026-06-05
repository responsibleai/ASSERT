# ASSERT Maintainer Assist System

ASSERT uses a small maintainer-assist agent system as tooling for repository upkeep. It is not user-facing product behavior; it helps the repository maintainer keep PRs reviewed and the docs site healthy while keeping all external writes under explicit human control.

This is a reusable pattern: other OSS maintainers can fork it.

## What lives here

- [Main system definition](../../AGENTS.md) — see the `Maintainer assist pattern (Copilot CLI + agents)` section, including the `Default state: observation mode` rules and narrow write exceptions for the dev-maintainer agent.
- [Agent specs](../../.github/agents/) — definitions for the dev-maintainer and designer agents.
- [Skill specs](../../.github/skills/) — two reusable skill definitions (`audit-pr`, `ux-audit`).
- [Public-safe inboxes](inbox/) — observation templates for dev-maintainer and designer outputs, plus a shared run log.

## Public-safe inboxes

The public inboxes ship as header-only templates:

- `inbox/dev-inbox.md` — PR and issue audit findings. **The dev-maintainer agent's two narrow write exceptions are active by default**, so this inbox begins receiving observation rows and audit summaries as soon as the dev-maintainer's recurring loop runs post-merge.
- `inbox/designer-inbox.md` — docs-site and sample UX findings. Empty until the designer agent is activated.
- `inbox/run-log.md` — one line per observation-loop pass (agent, items observed, items logged, anomalies).

The designer inbox uses placeholder rows only. Do not add real entries unless the maintainer explicitly activates the designer agent and approves the destination.

## Observation mode

Observation mode is the default state.

In this state:

- Agents observe only, **with two narrow exceptions held by the dev-maintainer agent** (see [`AGENTS.md`](../../AGENTS.md) §"Narrow write exceptions"):
  1. Audit-only PR comments (technical observations; never an approving or request-changes review).
  2. Reviewer requests on PRs unassigned past the 24h escalation window (assigns existing CODEOWNERS only).
- No PRs are auto-opened.
- No approvals, no merges, no label changes, no issue closures.
- No issue comments are auto-posted. Only dev-maintainer audit comments on existing PRs are permitted.

The designer agent has **no** write exceptions in observation mode. Activating broader writes for either agent — including new write capabilities for the dev-maintainer — requires explicit action by the maintainer per the activation procedure in `AGENTS.md`.

## Customer-safe terminology

Public-facing ASSERT docs use customer-safe terminology: behavior, dataset, test cases, OpenTelemetry, OpenInference, and spec-driven scoring. The agents and skills above use the same vocabulary.
