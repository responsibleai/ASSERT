# Adaptive Eval Agent Orientation

This file is for coding assistants such as GitHub Copilot, Claude Code, Cursor, and similar tools. It gives a short, customer-safe map of this preview repository.

## What this repo is

Adaptive Eval is a local-first, spec-driven evaluation harness for AI agents. A developer writes an eval spec, the pipeline generates targeted test cases, runs them against a target, and judges the resulting inference outputs (conversations or agent actions) against the spec.

Use this mental model:

```text
eval spec -> behavior categories -> test cases -> execute target -> judge -> artifacts
```

## Safety and privacy rules

- Never read, print, commit, summarize, or infer values from `.env` or other local environment files.
- Use placeholder names such as `AZURE_API_KEY` and `AZURE_API_BASE`; never invent or expose credential values.
- Do not recommend committing generated artifacts, local traces, `.venv`, logs, or `.env` files.
- Treat this repo as a customer-preview distribution. Keep contributions customer-safe — avoid any internal-only planning, prioritization, or organizational content.

## Authoritative files

Start with these files:

- `README.md` - customer-facing overview and quickstart.
- `docs/quickstart.md` - LangGraph travel planner walkthrough.
- `docs/targets/README.md` - target decision tree (rendered by default when browsing `docs/targets/`).
- `docs/targets/callable.md` - Python callable target for any agent or multi-agent system, with OpenTelemetry trace capture as the recommended integration path.
- `docs/targets/model-and-tools.md` - Prompt Agent target (hosted model + system prompt + optional tool schema; runtime owns the tool-call loop).
- `docs/config/schema.md` - current YAML schema reference.
- `examples/README.md` - example selection guide.

## Current preview terminology

Use the developer-friendly behaviors in prose, and mention current YAML keys when needed.

| Behavior to explain | Current YAML / artifact |
|---|---|
| Eval spec | `behavior.name`, `behavior.description` in `eval_config.yaml` |
| Target description | `context` |
| Variations | `dimensions` |
| Behavior categories | `pipeline.systematize`, `taxonomy.json` |
| Test cases | `pipeline.test_set`, `test_set.jsonl` |
| Execute | `pipeline.inference`, `inference_set.jsonl` |
| Target | `pipeline.inference.target` |
| Trace capture | `target.trace` |
| Judge | `pipeline.judge`, `scores.jsonl` |
| Metrics | `metrics.json` |

Do not rename schema fields unless explicitly asked. Some naming is still evolving, but the preview docs should stay aligned with the current branch.

## Target selection

When helping a developer choose a target:

1. If they have any agent or multi-agent system with a Python entry function (frameworks like LangGraph / CrewAI / OpenAI Agents SDK / DSPy / LlamaIndex / AutoGen / MAF, or custom orchestration), use `target.callable` **with `target.trace`** so Phoenix/OpenInference (or the agent's own OTel SDK spans) feed tool calls, routing, and intermediate decisions to the judge. This is the recommended integration path.
2. If they have a plain Python function that wraps a hosted model, use `target.callable` (still with OTel trace capture if the wrapper does anything meaningful — model call, retry, post-processing — that the judge should see).
3. If they have a hosted model with a system prompt and optional tools, use `target.model` and optional `target.tools`.
4. Simulated tools are useful for Prompt Agent setups (declared in YAML, runtime owns the loop) before real tool backends exist. They are not a replacement for evaluating a real agent or multi-agent system.

**Terminology divergence to know about**: in customer-facing docs we call `target.model + target.tools` the **Prompt Agent target** (the agent is declared in YAML; the runtime owns the tool-call loop). In code, the corresponding session class is `HostedSession` (`assert_ai/core/session.py`). Use the customer-facing name in docs and the class name in code references — this divergence is intentional and not worth renaming.

Recommend a plain callable without `target.trace` only when the target is a black-box API that cannot be instrumented, or for quick pipeline smoke tests. Flag this as a customization fallback, not the recommended path.

Do not recommend an external connector path for customer-preview onboarding.

## Preferred setup commands

For preview customers, use `pip` in setup instructions:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[otel,langgraph]"
cp .env.example .env

# Create a config interactively, or use an existing one
assert-ai init --model azure/gpt-5.4
# or run the flagship example directly
assert-ai run --config examples/travel_planner_langgraph/eval_config.yaml
```

Use the PowerShell equivalent on Windows:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[otel,langgraph]"
Copy-Item .env.example .env

# Create a config interactively, or use an existing one
assert-ai init --model azure/gpt-5.4
# or run the flagship example directly
assert-ai run --config examples/travel_planner_langgraph/eval_config.yaml
```

## How to help with common tasks

### Add an eval for a new agent

1. Ask what target shape the developer has: framework agent, custom runtime, Python function, or hosted model.
2. **Fastest path:** run `assert-ai init --model <litellm-model>` (or `assert-ai init --model <litellm-model> --describe "..."`) to create a config interactively with an LLM assistant. Use `--from <existing_config>` to edit/extend an existing config.
3. **Manual path:** Create or adapt an eval spec in `behavior.description` inside a YAML config.
4. Add `context` describing the agent, tools, users, and constraints.
5. Add `dimensions` only when systematic variation matters.
6. Configure the target in `pipeline.inference.target`.
7. Add judge dimensions with concrete descriptions and rubrics.
8. Run `assert-ai run --config <path>`.

### Debug a failure

Read artifacts in this order:

1. `metrics.json`
2. `scores.jsonl`
3. `inference_set.jsonl`
4. Phoenix/OpenInference traces, if configured
5. `config.yaml`

Look for judge evidence, cited turns, tool calls, routing decisions, and trace references.

### Update docs

Keep docs customer-safe. Prefer improving:

- `README.md`
- `docs/quickstart.md`
- `docs/targets/*.md`
- `docs/writing-eval-specs.md`
- `docs/reading-results.md`
- `examples/README.md`

Do not reintroduce internal-only planning docs into this customer-preview distribution.

## Paste-in prompt for end users

End users can paste the following block into their AI assistant to get the same orientation this file gives you:

```text
You are helping me with the Adaptive Eval repo (https://github.com/microsoft/adaptive-eval).

Adaptive Eval is a local-first, spec-driven evaluation pipeline for AI agents. The mental model:

  eval spec -> behavior categories -> test cases -> execute target -> judge -> artifacts

Key facts:
- The canonical CLI entrypoint is `assert-ai`; legacy CLI aliases are intentionally not supported. Configs live in `examples/`. Artifacts land in `artifacts/results/<suite>/<run>/`.
- For any agent or multi-agent system with a Python entry function, use `target.callable` with `target.trace`.
  OpenTelemetry trace capture (Phoenix/OpenInference for 33+ frameworks, or your own OTel SDK spans) is the recommended integration path so the judge can score tool calls and routing, not just final text.
- For a hosted model with a system prompt and optional tools, use `target.model` and `target.tools`.
- Read `README.md`, `docs/quickstart.md`, `docs/targets/README.md`, `docs/targets/callable.md`, and
  `docs/config/schema.md` before suggesting changes to YAML schema.
- Never read or print values from `.env`. Use placeholder names like AZURE_API_KEY and AZURE_API_BASE.
- Keep all suggestions customer-safe.

When I ask for help, prefer concrete file paths, runnable commands, and the YAML keys defined in docs/config/schema.md.
```

## Output style for coding agents

- Be concise and action-oriented.
- Prefer runnable commands and real paths.
- Use forward slashes in customer-facing docs unless the block is explicitly PowerShell-only.
- Frame OpenTelemetry trace capture as the recommended integration path for any non-trivial agent — not as an optional add-on. A plain callable without traces is a customization fallback for black-box APIs or pipeline smoke tests.
- If a command needs credentials, say which environment variable names are required but do not inspect or print their values.

---

# Autonomous agent system (operator-only)

This section defines a separate, operator-only autonomous-agent system used to maintain the ASSERT repository, monitor its docs and feedback surfaces, and triage operator-side communication. It is **not** part of the customer-facing product, and most contributors will never need to interact with it.

The Copilot/orientation guidance above this section applies to every contributor. Everything below applies only to the operator running these agents on their local workstation.

## Default state: VACATION MODE

VACATION MODE is the default and currently only enabled state. In VACATION MODE:

- All five agents are observation-only by default.
- No PR approvals. No merges. No issue files. No Discussion replies. No support replies. No social posts. No Outlook or Teams sends.
- Each agent logs its findings as rows in an inbox file. The inbox is markdown; entries are reviewed by Chang manually.

Activation of any agent requires explicit, agent-specific approval by Chang. There is no global "activate all" switch.

### Narrow write exceptions (dev-maintainer only)

To prevent PRs from sitting unreviewed while Chang is on vacation, the **dev-maintainer agent** has two narrowly-scoped write powers that are always active (not gated on activation):

1. **Post audit-only comments** on open PRs — technical findings, severity, suggested next steps. Never an approval, never a request-changes review, never a merge, never a label change.
2. **Request review** (assign reviewers) from CODEOWNERS when a PR has been open with no requested reviewers, or when an existing reviewer has not responded within the escalation window.

Both writes use the customer-safe terminology defined elsewhere in this document.

### 24-hour escalation rule

The dev-maintainer agent enforces this rule on every observation pass:

| PR age (no review action) | Action |
|---|---|
| < 24h | Observe only; log to dev-inbox. |
| ≥ 24h, no reviewer requested | Request review from a CODEOWNER on the affected path (see routing below). |
| ≥ 72h, reviewer requested but no response | Post a polite escalation comment tagging a second CODEOWNER from the same path. |
| ≥ 7 days, still no response | Escalate to Chang as last resort. |

### Reviewer routing logic

When picking a reviewer to request or ping:

1. Read the effective CODEOWNERS list for the PR's changed paths.
2. **Exclude the PR author.**
3. **Exclude any owner listed as unavailable in [`.github/CODEOWNERS-VACATIONS.md`](.github/CODEOWNERS-VACATIONS.md).**
4. **Exclude Chang** unless every other co-owner has been excluded by the rules above. Chang is the fallback-only reviewer because his bandwidth is intentionally constrained during vacation.
5. Pick from the remaining candidates. Prefer admins. If the path has multiple eligible owners, pick the one not recently pinged.

### Other four agents stay observation-only

The designer, feedback, pm, and comms agents have **no** write exceptions in vacation mode. Everything they produce lands in an inbox only.

## Sole human approver

**Chang** is the sole human approver for every external write, send, merge, or activation by any agent in this system. This applies in vacation mode and after activation: even after an agent is activated, the scope of what it may write externally is defined per activation by Chang.

## The five agents

All agent specs live in [`.github/agents/`](.github/agents/):

| Agent | Spec | Inbox | Inbox location |
|---|---|---|---|
| Dev maintainer | [`dev-maintainer.md`](.github/agents/dev-maintainer.md) | `dev-inbox.md` | `docs/agents/inbox/` (public-safe) |
| Designer | [`designer.md`](.github/agents/designer.md) | `designer-inbox.md` | `docs/agents/inbox/` (public-safe) |
| Feedback | [`feedback.md`](.github/agents/feedback.md) | `feedback-inbox.md` | `docs/agents/inbox/` (public-safe, anonymized) |
| PM | [`pm.md`](.github/agents/pm.md) | `pm-inbox.md` | operator-side internal workspace (not in this repo) |
| Comms | [`comms.md`](.github/agents/comms.md) | `comms-inbox.md` | operator-side internal workspace (not in this repo) |

The dev-maintainer, designer, and feedback agents produce public-safe outputs that live in this repository under `docs/agents/inbox/`. The PM and comms agents produce sensitive outputs (competitive positioning, operator-private communication drafts) that do not belong in a public repository; their inboxes live in the operator's internal workspace.

## Reusable skills

All skill specs live in [`.github/skills/`](.github/skills/). Each skill defines a single methodology with an explicit output format:

- [`audit-pr.md`](.github/skills/audit-pr.md) — review a PR for behavior naming, OpenInference trace attributes, and dataset coverage. Output: pass/fail per dimension + one-line summary.
- [`competitive-scan.md`](.github/skills/competitive-scan.md) — scan public eval-framework sources for signal. Output destination: internal PM inbox.
- [`strategy-synthesis.md`](.github/skills/strategy-synthesis.md) — distill signals into targeted positioning diffs. Output destination: internal strategy docs.
- [`ux-audit.md`](.github/skills/ux-audit.md) — walk the ASSERT golden path and score each step on clarity, delight, friction, and error quality.
- [`feedback-synthesis.md`](.github/skills/feedback-synthesis.md) — extract structured signal from transcripts and support threads. Always anonymized.
- [`draft-reply.md`](.github/skills/draft-reply.md) — draft a comms response in Chang's voice.
- [`file-feedback-issue.md`](.github/skills/file-feedback-issue.md) — **post-vacation only**. Routes findings to the correct downstream agent.

## Public-safe inboxes

Public inboxes are header-only markdown templates that live under `docs/agents/inbox/` and remain empty until activation:

- [`dev-inbox.md`](docs/agents/inbox/dev-inbox.md)
- [`designer-inbox.md`](docs/agents/inbox/designer-inbox.md)
- [`feedback-inbox.md`](docs/agents/inbox/feedback-inbox.md)

See [`docs/agents/README.md`](docs/agents/README.md) for the operator-facing index of the agent system.

## Customer-safe terminology

All files in this autonomous-agent system that live under `responsibleai/ASSERT` use the same customer-facing vocabulary as the rest of the repo: `behavior`, `eval spec`, `dataset`, `test cases`, `OpenTelemetry`, `OpenInference`, `spec-driven scoring`. Internal shorthand stays out of public files.

## Activation procedure

When Chang activates an agent:

1. Pick the agent (one at a time).
2. Read its spec file and confirm the trigger conditions, output destination, and skills used are still current.
3. Define the activation scope: what external writes are now allowed, on what cadence, and with what review gate.
4. Record the activation decision (date, agent, scope) in a place Chang can audit later.
5. The agent transitions from observation-only to the activated scope. Everything outside that scope still routes to the inbox.

Until step 5 is recorded for a given agent, that agent remains in VACATION MODE.
