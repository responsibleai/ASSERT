# Adaptive Eval Agent Orientation

This file is for coding assistants such as GitHub Copilot, Claude Code, Cursor, and similar tools. It gives a short, customer-safe map of this private-preview repository.

## What this repo is

Adaptive Eval is a local-first, spec-driven evaluation harness for AI agents. A developer writes an eval spec, the pipeline generates targeted test cases, runs them against a target, and judges the resulting conversations against the spec.

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
- `docs/targets/overview.md` - target decision tree.
- `docs/targets/callable.md` - Python callable target for any agent or multi-agent system, with an optional OpenTelemetry trace-capture upgrade.
- `docs/targets/model-and-tools.md` - hosted model and simple model+tools target.
- `CONFIG_REFERENCE.md` - current YAML schema reference.
- `examples/README.md` - example selection guide.

## Current preview terminology

Use the developer-friendly concepts in prose, and mention current YAML keys when needed.

| Concept to explain | Current YAML / artifact |
|---|---|
| Eval spec | `concept.name`, `concept.md`, `<name>.md` |
| Target description | `context` |
| Variations | `factors` |
| Behavior categories | `pipeline.policy`, `policy.json` |
| Test cases | `pipeline.seeds`, `seeds.jsonl` |
| Execute | `pipeline.rollout`, `transcripts.jsonl` |
| Target | `pipeline.rollout.target` |
| Trace capture | `target.trace` |
| Judge | `pipeline.judge`, `scores.jsonl` |
| Metrics | `metrics.json` |

Do not rename schema fields unless explicitly asked. Some naming is still evolving, but the private-preview docs should stay aligned with the current branch.

## Target selection

When helping a developer choose a target:

1. If they have any agent or multi-agent system with a Python entry function (frameworks like LangGraph / CrewAI / OpenAI Agents SDK / DSPy / LlamaIndex / AutoGen / MAF, or custom orchestration), use `target.callable`. Trace capture through Phoenix/OpenInference is an optional upgrade — recommend it when the judge would benefit from seeing tool calls, routing, and intermediate decisions.
2. If they have a plain Python function that wraps a hosted model, use `target.callable`.
3. If they have a hosted model with a system prompt and optional tools, use `target.model` and optional `target.tools`.
4. Simulated tools are useful for simple prompt-agent setups before real tool backends exist. They are not a replacement for evaluating a real agent or multi-agent system.

Do not require customers to understand OpenTelemetry before their first eval. The plain callable path works without trace capture.

Do not recommend an external connector path for customer-preview onboarding.

## Preferred setup commands

For preview customers, prefer `pip` over `uv` in setup instructions:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[otel,langgraph]"
cp .env.example .env
p2m run --config examples/travel_planner_langgraph/eval_config.yaml
```

Use the PowerShell equivalent on Windows:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[otel,langgraph]"
Copy-Item .env.example .env
p2m run --config examples/travel_planner_langgraph/eval_config.yaml
```

## How to help with common tasks

### Add an eval for a new agent

1. Ask what target shape the developer has: framework agent, custom runtime, Python function, or hosted model.
2. Create or adapt an eval spec markdown file.
3. Add `context` describing the agent, tools, users, and constraints.
4. Add `factors` only when systematic variation matters.
5. Configure the target in `pipeline.rollout.target`.
6. Add judge dimensions with concrete descriptions and rubrics.
7. Run `p2m run --config <path>`.

### Debug a failure

Read artifacts in this order:

1. `metrics.json`
2. `scores.jsonl`
3. `transcripts.jsonl`
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
- The CLI entrypoint is `p2m`. Configs live in `examples/`. Artifacts land in `artifacts/results/<suite>/<run>/`.
- For any agent or multi-agent system with a Python entry function, use `target.callable`.
  OpenTelemetry trace capture (Phoenix/OpenInference) is an optional upgrade — not required.
- For a hosted model with a system prompt and optional tools, use `target.model` and `target.tools`.
- Read `README.md`, `docs/quickstart.md`, `docs/targets/overview.md`, `docs/targets/callable.md`, and
  `CONFIG_REFERENCE.md` before suggesting changes to YAML schema.
- Never read or print values from `.env`. Use placeholder names like AZURE_API_KEY and AZURE_API_BASE.
- Keep all suggestions customer-safe.

When I ask for help, prefer concrete file paths, runnable commands, and the YAML keys defined in CONFIG_REFERENCE.md.
```

## Output style for coding agents

- Be concise and action-oriented.
- Prefer runnable commands and real paths.
- Use forward slashes in customer-facing docs unless the block is explicitly PowerShell-only.
- Explain OpenTelemetry as optional trace capture; do not require users to understand it before running the first eval.
- If a command needs credentials, say which environment variable names are required but do not inspect or print their values.
