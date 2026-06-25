# Getting Started

This guide covers installation and your first end-to-end evaluation run.

## Prerequisites

- Python 3.11+
- pip
- Model credentials in environment variables (for example `AZURE_API_KEY` and `AZURE_API_BASE` for Azure OpenAI)

## Install with a quickstart example: LangGraph travel planner

The flagship example evaluates a multi-tool LangGraph travel planner. The target is reached through `target.callable` — the same integration boundary you would use for any agent or multi-agent system — and Phoenix/OpenInference auto-instrumentation captures the agent's OpenTelemetry spans so the judge can cite tool calls and routing decisions. This is the recommended integration shape for any non-trivial agent.

### Recommended install path

bash (macOS / Linux):

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[otel,langgraph]"
cp .env.example .env
```

Edit `.env` with credentials for your provider. Defaults match the example's `azure/...` model. Any LiteLLM provider (OpenAI, Anthropic, Bedrock, Vertex, Ollama, and others) works.

PowerShell (Windows):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[otel,langgraph]"
Copy-Item .env.example .env
```

### Run your first evaluation

The example's `auto_trace.py` calls `assert_ai.auto_trace.enable()`, which installs the available OpenInference instrumentors locally so the judge can cite tool calls, routing decisions, model calls, and latency as evidence. It does **not** start a Phoenix server.

`phoenix serve` is optional — only run it if you want a browser UI to inspect the traces visually. The eval runs and the judge see the same span data either way.

bash (macOS / Linux):

```bash
phoenix serve  # optional: trace UI on http://localhost:6006
assert-ai run --config examples/travel_planner_langgraph/eval_config.yaml
```

PowerShell (Windows):

```powershell
phoenix serve  # optional: trace UI on http://localhost:6006
assert-ai run --config examples/travel_planner_langgraph/eval_config.yaml
```

Check run status:

PowerShell (Windows):

```powershell
assert-ai results status travel-planner-langgraph-v1 demo-1
```

bash (macOS / Linux):

```bash
assert-ai results status travel-planner-langgraph-v1 demo-1
```

Artifacts are written under:

```text
artifacts/results/travel-planner-langgraph-v1/demo-1/
```

### Codespaces / VS Code Dev Containers

[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/microsoft/ASSERT)

The repo includes a minimal dev container for the LangGraph quickstart. It installs `.[otel,langgraph,dev]`, copies `.env.example` to `.env` if needed, and forwards Phoenix on port `6006`. After container setup, add your provider credentials to `.env` and run the same `assert-ai run` command.

PowerShell (Windows) — full sequence:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[otel,langgraph]"
Copy-Item .env.example .env

phoenix serve  # optional
assert-ai run --config examples/travel_planner_langgraph/eval_config.yaml
assert-ai results status travel-planner-langgraph-v1 demo-1
```

## What just happened

1. `systematize` expanded the behavior spec into behavior categories.
2. `test_set` generated prompt and scenario test cases.
3. `inference` executed the target for each case.
4. `judge` produced verdicts, evidence, and aggregate metrics.

What the quickstart does:

| Step | Developer behavior | Current YAML / artifact |
|---|---|---|
| 1 | **Eval spec**: plain-English behavior requirements | `behavior.name` and `behavior.description` live inline in `eval_config.yaml` |
| 2 | **Behavior categories**: generated failure-mode taxonomy | `pipeline.systematize` writes `taxonomy.json` |
| 3 | **Test cases**: prompts and multi-turn scenarios | `pipeline.test_set` writes `test_set.jsonl` |
| 4 | **Execute**: run the agent and capture traces | `pipeline.inference.target.callable` + `target.trace` write `inference_set.jsonl` |
| 5 | **Judge**: score against your rubric | `pipeline.judge.dimensions` writes `scores.jsonl` and `metrics.json` |

### CLI helper assistant to create your own config

Don't want to write YAML by hand? `assert-ai init` starts a conversational LLM assistant that asks about your agent, eval goals, and constraints, then proposes a complete config YAML file to use for your evaluations.

`assert-ai init` needs an LLM to power the conversation. Pass `--model` with any [LiteLLM model string](https://docs.litellm.ai/docs/providers) and make sure the matching API key is set in your `.env` file (loaded by default) or environment:

```bash
assert-ai init --model azure/gpt-5.4
# or skip the first question:
assert-ai init --model azure/gpt-5.4 --describe "A customer-support chatbot with order-lookup and refund tools"
# or edit/extend an existing config:
assert-ai init --model azure/gpt-5.4 --from examples/travel_planner_langgraph/eval_config.yaml
```

See [CLI Commands](cli/commands.md) for the full option reference.

- To learn the config format, see [Config Overview](config/overview.md).
- To inspect outputs in detail, see [Results Guide](guides/results.md).
- To use the local web viewer, see [Run the Local UI Viewer Application](guides/run-local-viewer.md).

## Authenticating Azure OpenAI with Managed Identity

If you would rather not provision and rotate an `AZURE_API_KEY`, ASSERT can call
Azure OpenAI using Entra ID (Microsoft Managed Identity / `az login`) instead.
This works for any `azure/*` model string and uses LiteLLM's native
`azure_ad_token_provider` hook under the hood — no other config changes required.

### Install the optional dependency

```bash
python -m pip install -e ".[azure-aad]"
```

This pulls in `azure-identity` and lets ASSERT mint bearer tokens through
`DefaultAzureCredential`.

### Grant the caller the right RBAC role

On the target Azure OpenAI resource, give the caller identity (your user, a
managed identity, or a service principal) the **Cognitive Services OpenAI User**
role. Without this role every request will return `401`.

### Pick an auth mode

Auth resolution at process start follows a single precedence rule:

| You set | Mode resolved | When to use |
|---|---|---|
| `ASSERT_AZURE_USE_AAD=1` | `aad` (explicit AAD) | Production: AAD only, even if a key is also in the env. Missing `azure-identity` fails loud. |
| `AZURE_API_KEY=...` (and the flag above is unset) | `key` | Today's default. Zero behavior change. |
| Neither | `aad-fallback` | Best-effort AAD. If `azure-identity` is missing, LiteLLM's own error is rewritten to suggest the install. |

`AZURE_API_BASE` is still required so LiteLLM knows which Azure OpenAI endpoint
to call.

The same auth mode also applies to `azure_ai/*` LiteLLM routes, including
hosted Azure AI Foundry agents (`azure_ai/agents/<AGENT_ID>`). Those routes
need `AZURE_AI_API_BASE` set to the Foundry project endpoint instead of
`AZURE_API_BASE`. No extra setup beyond `pip install -e ".[azure-aad]"` and
`az login` (or Service Principal env vars).

### Local development with `az login`

```bash
az login
export ASSERT_AZURE_USE_AAD=1
unset AZURE_API_KEY  # optional — the flag wins regardless
assert-ai run --config examples/azure_managed_identity/eval_config.yaml
```

### Running on Azure (App Service, AKS, Container Apps, VM)

Assign a managed identity to the workload, grant it the OpenAI User role,
and set `ASSERT_AZURE_USE_AAD=1`. To pin a specific user-assigned identity
when multiple are attached, set `AZURE_CLIENT_ID` to its client ID;
`DefaultAzureCredential` will pick it up automatically.

### Troubleshooting

- `LLMAuthError: ... azure-identity package is not installed` — run
  `pip install -e ".[azure-aad]"` (or `assert-ai[azure-aad]` if you installed from PyPI).
- `401` with a hint about *Cognitive Services OpenAI User* — the credential
  resolved, but the identity is missing the RBAC role on the resource.
- A 401 that mentions the install hint instead — you are in `aad-fallback`
  mode without `azure-identity`. Install the extra or set `AZURE_API_KEY`.
