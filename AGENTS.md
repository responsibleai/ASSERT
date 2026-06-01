# ASSERT Agent Orientation

This file is for coding assistants such as GitHub Copilot, Claude Code, Cursor, and similar tools. It gives a short, customer-safe map of this preview repository.

## What this repo is

ASSERT is a local-first, spec-driven evaluation harness for AI agents. A developer writes an eval spec, the pipeline generates targeted test cases, runs them against a target, and judges the resulting inference outputs (conversations or agent actions) against the spec.

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
- `docs/getting-started.md` - LangGraph travel planner walkthrough.
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
assert-ai init --model azure/gpt-4o
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
assert-ai init --model azure/gpt-4o
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
- `docs/getting-started.md`
- `docs/targets/*.md`
- `docs/guides/create-evaluation.md`
- `docs/guides/results.md`
- `examples/README.md`

Do not reintroduce internal-only planning docs into this customer-preview distribution.

## Paste-in prompt for end users

End users can paste the following block into their AI assistant to get the same orientation this file gives you:

```text
You are helping me with the ASSERT repo (https://github.com/responsibleai/ASSERT).

ASSERT is a local-first, spec-driven evaluation pipeline for AI agents. The mental model:

  eval spec -> behavior categories -> test cases -> execute target -> judge -> artifacts

Key facts:
- The canonical CLI entrypoint is `assert-ai`; legacy CLI aliases are intentionally not supported. Configs live in `examples/`. Artifacts land in `artifacts/results/<suite>/<run>/`.
- For any agent or multi-agent system with a Python entry function, use `target.callable` with `target.trace`.
  OpenTelemetry trace capture (Phoenix/OpenInference for 33+ frameworks, or your own OTel SDK spans) is the recommended integration path so the judge can score tool calls and routing, not just final text.
- For a hosted model with a system prompt and optional tools, use `target.model` and `target.tools`.
- Read `README.md`, `docs/getting-started.md`, `docs/targets/README.md`, `docs/targets/callable.md`, and
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
