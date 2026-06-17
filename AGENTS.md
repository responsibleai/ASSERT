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
- Use placeholder names such as `AZURE_API_KEY`, `AZURE_API_BASE`, `azure_ad_token`, and `azure_ad_token_provider`; never invent or expose credential values.
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

1. `scores.jsonl`
2. `inference_set.jsonl`
3. Phoenix/OpenInference traces, if configured
4. `config.yaml`
5. `metrics.json`

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
- Never read or print values from `.env`. Use placeholder names like AZURE_API_KEY, AZURE_API_BASE, azure_ad_token, and azure_ad_token_provider.
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

# Maintainer assist pattern (Copilot CLI + agents)

This section defines a reusable OSS maintainer-assist pattern: a small set of Copilot CLI agents the repository maintainer runs on a scheduled loop on an always-on host (see [Where to run the loop](#where-to-run-the-loop)), plus `.github/CODEOWNERS` routing, to keep the repo healthy when the maintainer can't review every PR within hours. Technical PRs still get an audit pass; stale review requests get re-routed to an available code owner.

It is **not** part of the ASSERT product. Contributors do not need to interact with it. Other OSS maintainers are welcome to fork the pattern.

The Copilot/orientation guidance above this section applies to every contributor. Everything below describes the maintainer-assist agents.

## Default state: observation mode

Observation mode is the default and currently only enabled state. In observation mode:

- The **designer** agent is **off by default** — it has no recurring schedule and no triggers active. When the maintainer adds a schedule for the designer, it walks the golden path and writes findings to `designer-inbox.md`. External writes (filing issues, opening docs PRs) require a separate explicit activation per the activation procedure.
- The **dev-maintainer** agent is observation-only **except** for the two narrow write exceptions described below — those are active by default and do not require activation.
- No PR approvals. No merges. No issue files. No Discussion replies. No label changes.

Broader activation of either agent — including any *new* write capability beyond the dev-maintainer's two narrow exceptions — requires explicit, agent-specific approval by the maintainer. There is no global "activate all" switch.

### Narrow write exceptions (dev-maintainer only)

To prevent PRs from sitting unreviewed when the maintainer is unavailable, the **dev-maintainer agent** has two narrowly-scoped write powers that are always active (not gated on activation):

1. **Post audit-only comments** on open PRs — technical findings, severity, suggested next steps. Never an approval, never a request-changes review, never a merge, never a label change.
2. **Request review** (assign reviewers) from CODEOWNERS when a PR has been open with no requested reviewers, or when an existing reviewer has not responded within the escalation window.

Both writes use the customer-safe terminology defined elsewhere in this document.

### 24-hour escalation rule

The dev-maintainer agent enforces this rule on every observation pass:

| PR age (no review action) | Action |
|---|---|
| < 24h | Observe only; log to dev-inbox. |
| ≥ 24h, no reviewer requested | Request review from a CODEOWNER on the affected path (see routing below). |
| ≥ 72h, reviewer requested but no response | Request review from a *second* CODEOWNER on the same path (uses narrow write #2 again — GitHub's review-request mechanism notifies the new reviewer directly). |
| ≥ 7 days, still no response | Escalate to the fallback admin (repository maintainer) as last resort. |

### Where to run the loop

The escalation windows above are wall-clock thresholds, so the loop only helps if it runs somewhere that stays up while the maintainer is away — which is the exact situation the escalation is designed for. Running it on the maintainer's own workstation defeats the purpose: if the maintainer is offline (vacation, travel, off-grid), so is their laptop, and the 24h / 72h passes never fire.

Run the loop on an **always-on host** instead:

- a small always-on VM (the maintainer's own infrastructure), or
- a scheduled CI job or cron, or
- a scheduled GitHub Action (`on: schedule:`), which needs no separate host at all.

`.github/CODEOWNERS` (GitHub-native review routing) already covers the baseline case on its own and keeps working regardless of where — or whether — this loop runs. Treat the agent loop as an enhancement layered on top of CODEOWNERS, not a replacement for it.

This repo ships that enhancement as a reference implementation: the scheduled workflow `.github/workflows/review-escalation.yml` runs `.github/scripts/escalate_reviews.py`, which applies the windows and routing above deterministically (no LLM) on GitHub's own always-on schedule. The LLM `audit-pr` pass remains a separate concern a maintainer can run from any host.

### Reviewer routing logic

When picking a reviewer to request or ping:

1. Read the effective CODEOWNERS list for the PR's changed paths.
2. **Exclude the PR author.**
3. **Exclude any owner whose GitHub status is set to "busy" / "out of office"** at the time the agent runs (the agent reads the GraphQL `user.status` field for each candidate; owners keep this in sync themselves).
4. **Exclude the fallback admin** unless every other co-owner has been excluded by the rules above. The fallback admin is the reviewer of last resort. **Never request the PR author** — if the only owner of a path is the author (e.g. the catch-all owner authored the PR), the agent makes no request and logs the PR for manual escalation rather than pinging the author.
5. Pick deterministically from the remaining candidates: the owner covering the most changed paths, then alphabetical order. The reference Action (`.github/workflows/review-escalation.yml`) is stateless, so it uses this deterministic order in place of "least recently pinged"; a stateful host may substitute ping-history. For the 72h second-owner and 7d fallback steps, owners already requested are excluded and the next is chosen by the same order.

### Designer agent stays observation-only

The designer agent has **no** write exceptions in observation mode. When the maintainer adds a schedule for it, the designer produces inbox rows only; any external writes (filing issues, opening docs PRs, posting comments) require explicit activation per the activation procedure below.

## Sole human approver

The **repository maintainer** is the sole human approver for every external write, merge, label change, or activation by either agent. This applies in observation mode and after activation: even after an agent is activated, the scope of what it may write externally is defined per activation by the maintainer.

## The two agents

Both agent specs live in [`.github/agents/`](.github/agents/):

| Agent | Spec | Inbox |
|---|---|---|
| Dev maintainer | [`dev-maintainer.md`](.github/agents/dev-maintainer.md) | [`dev-inbox.md`](docs/agents/inbox/dev-inbox.md) |
| Designer | [`designer.md`](.github/agents/designer.md) | [`designer-inbox.md`](docs/agents/inbox/designer-inbox.md) |

Both agents produce public-safe outputs that live in this repository under `docs/agents/inbox/`. The dev-maintainer's audit findings on public PRs are technical observations; the designer's UX findings reference public docs and example surfaces. No operator-private content lives in either inbox.

## Reusable skills

Both skill specs live in [`.github/skills/`](.github/skills/). Each skill defines a single methodology with an explicit output format:

- [`audit-pr.md`](.github/skills/audit-pr.md) — review a PR for behavior naming, OpenInference trace attributes, and dataset coverage. Output: pass/fail per dimension + one-line summary.
- [`ux-audit.md`](.github/skills/ux-audit.md) — walk the ASSERT golden path and score each step on clarity, delight, friction, and error quality.

## Public-safe inboxes

Public inboxes live under `docs/agents/inbox/`:

- [`dev-inbox.md`](docs/agents/inbox/dev-inbox.md) — begins receiving observation rows and audit summaries from the dev-maintainer agent as soon as its recurring loop runs post-merge, because the dev-maintainer's two narrow write exceptions are active by default.
- [`designer-inbox.md`](docs/agents/inbox/designer-inbox.md) — header-only template by default. When the maintainer adds a schedule for the designer agent, this inbox begins receiving observation rows. The designer has no external writes; filing issues or opening docs PRs from these findings requires separate activation per the activation procedure.
- [`run-log.md`](docs/agents/inbox/run-log.md) — one-line status entry per observation-loop pass.

See [`docs/agents/README.md`](docs/agents/README.md) for the contributor-facing index of the agent system.

## Customer-safe terminology

All files in this maintainer-assist system use the same customer-facing vocabulary as the rest of the repo: `behavior`, `eval spec`, `dataset`, `test cases`, `OpenTelemetry`, `OpenInference`, `spec-driven scoring`. Internal shorthand stays out of public files.

## Activation procedure

When the maintainer activates a broader agent capability:

1. Pick the agent and the specific capability (one at a time).
2. Read the agent spec and confirm trigger conditions, output destination, and skills used are still current.
3. Define the activation scope: what external writes are now allowed, on what cadence, and with what review gate.
4. Record the activation decision (date, agent, scope) in a place the maintainer can audit later.
5. The agent transitions from observation-only to the activated scope. Everything outside that scope still routes to the inbox.
