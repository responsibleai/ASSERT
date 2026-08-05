# Travel Planner — Foundry Hosted Agent (Responses) + Adaptive Eval

A [LangGraph](https://langchain-ai.github.io/langgraph/) multi-node travel
planner agent, hosted on Microsoft Foundry over the **Responses protocol** using
[`langchain_azure_ai.agents.hosting`](https://github.com/langchain-ai/langchain-azure/tree/main/libs/azure-ai/langchain_azure_ai/agents/hosting),
and evaluated end-to-end against the **deployed agent** with Adaptive Eval
(`assert-ai`).

Originally ported from the
[foundry-samples `langgraph/responses`](https://github.com/microsoft-foundry/foundry-samples/tree/main/samples/python/hosted-agents/langgraph/responses)
sample, then extended into a travel planner with an evaluation spec.

## What this sample demonstrates

The graph in [`agent.py`](agent.py) is a multi-node travel planner built with a
LangGraph `StateGraph`:

```text
intent_classifier ─▶ route_after_intent ─┬─▶ research ─▶ itinerary_optimizer ─▶ route_after_itinerary ─┬─▶ END
                                          │                                                              └─▶ clarification ─▶ END
                                          └─▶ clarification ─▶ END
```

- `intent_classifier` — extracts intent / destination / budget from the request.
- `research` — binds the travel tools to the model and runs a `ToolNode` to
  gather flights, hotels, weather, advisories, and a budget check.
- `itinerary_optimizer` — assembles a grounded itinerary from the tool results.
- `clarification` — asks a follow-up when the request is underspecified.

It registers five **simulated** tools (mock backends, deterministic responses)
so the example runs without real travel APIs:

- `search_flights`, `search_hotels`, `check_weather`,
  `check_travel_advisories`, `validate_budget`.

The compiled graph is hosted with `ResponsesHostServer`
([`main.py`](main.py)), which exposes the OpenAI-compatible Responses endpoint
at `/responses` and handles conversation history, streaming lifecycle events,
and tool-call surfacing automatically. Conversation state is managed
server-side by the platform via `previous_response_id` — there is no
application-side session storage.

## Project structure

```text
langgraph-foundry-hosted/
├── agent.py                # LangGraph travel planner graph + tools (build_graph / get_graph)
├── main.py                 # ResponsesHostServer entrypoint (imports get_graph)
├── auto_trace.py           # chat_sync: calls the DEPLOYED agent's Responses endpoint (eval target)
├── eval_config.yaml        # Adaptive Eval (assert-ai) spec for the travel planner
├── requirements.txt        # Python dependencies
├── agent.yaml              # Foundry hosted-agent (container) definition
├── agent.manifest.yaml     # Agent template manifest (model + env resolution)
├── azure.yaml              # azd deployment config (azure.ai.agent host)
├── Dockerfile              # Container image for the hosted agent
├── .dockerignore
├── .agentignore            # Files excluded from the deploy package
├── .env.example            # Environment variable template
└── README.md
```

## Prerequisites

- **Python 3.13+**
- **Azure subscription** with a Microsoft Foundry project
- **Foundry project endpoint** (e.g.
  `https://<resource>.services.ai.azure.com/api/projects/<project>`)
- **Model deployment** in Foundry (e.g. `gpt-5.4`)
- **Azure CLI** signed in: `az login`
- **Azure Developer CLI** (`azd`) for deployment
- **`assert-ai`** installed (from the repo root: `pip install -e ".[otel,langgraph]"`)
  to run the evaluation

## Environment variables

Copy the template and fill in the values (never commit `.env`):

```bash
cp .env.example .env             # Windows: Copy-Item .env.example .env
```

| Variable | Used by | Notes |
|---|---|---|
| `FOUNDRY_PROJECT_ENDPOINT` | `agent.py` (the graph's LLM calls) | Auto-injected in hosted containers; set manually only when running locally. |
| `AZURE_AI_MODEL_DEPLOYMENT_NAME` | `agent.py` | Must match a model deployment in your Foundry project. Defaults to `gpt-5.4`. |
| `FOUNDRY_AGENT_ENDPOINT` | `auto_trace.py` (eval target) | The deployed agent's Responses endpoint, printed by `azd deploy`. |
| `AZURE_API_BASE` | `assert-ai` (eval pipeline) | Azure OpenAI endpoint for the `azure/gpt-5.4` model that runs systematize / test generation / tester / judge. Separate from the agent above. |
| `AZURE_API_KEY` | `assert-ai` (eval pipeline) | API key for `AZURE_API_BASE`. Omit if using Entra ID (AAD) auth instead. |

> **ASSERT model auth:** the eval pipeline calls `azure/gpt-5.4` via LiteLLM
> for every stage, which is a *separate* credential from the hosted agent.
> Provide either an `AZURE_API_KEY` + `AZURE_API_BASE` pair, or use Entra ID
> auth (`pip install -e ".[azure-aad]"`, set `ASSERT_AZURE_USE_AAD=1`, and grant
> the caller the **Cognitive Services OpenAI User** role on the Azure OpenAI
> resource). See [`.env.example`](.env.example) for the full template.

## Run the agent host locally

```bash
cd examples/langgraph-foundry-hosted

# Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\Activate.ps1

# Install dependencies
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

# Configure environment (see table above), then start the server (port 8088)
python main.py
```

### Interacting with the local server

```bash
# Turn 1 — plan a trip
curl -X POST http://localhost:8088/responses \
  -H "Content-Type: application/json" \
  -d '{"input": "Plan a 3-night trip from Chicago to Miami for two adults, budget $1500."}'

# Turn 2 — chain via previous_response_id
curl -X POST http://localhost:8088/responses \
  -H "Content-Type: application/json" \
  -d '{"input": "Make it cheaper.", "previous_response_id": "REPLACE_WITH_PREVIOUS_RESPONSE_ID"}'
```

```powershell
(Invoke-WebRequest -Uri http://localhost:8088/responses -Method POST `
  -ContentType "application/json" `
  -Body '{"input": "Plan a 3-night trip from Chicago to Miami for two adults, budget $1500."}').Content
```

## Deploy to Foundry

For a **new** project, provision first, then deploy:

```bash
azd provision   # creates resources + model deployment
azd deploy      # builds the container and registers the hosted agent version
```

For an **existing** Foundry project, skip `azd provision` and set the project
coordinates in the azd environment before `azd deploy`:

```bash
azd env set AZURE_SUBSCRIPTION_ID          "<sub-id>"
azd env set AZURE_TENANT_ID                "<tenant-id>"
azd env set AZURE_RESOURCE_GROUP           "<resource-group>"
azd env set AZURE_LOCATION                 "<region>"          # e.g. eastus2
azd env set AZURE_AI_PROJECT_ID            "<full ARM project resource id>"
azd env set AZURE_AI_MODEL_DEPLOYMENT_NAME "gpt-5.4"           # an existing deployment
azd deploy --no-prompt
```

`azd` reads `azure.yaml`, `agent.yaml`, and `agent.manifest.yaml` to build and
upload the container, register the hosted agent version, and wire up the model
deployment and RBAC. The runtime model is resolved from
`AZURE_AI_MODEL_DEPLOYMENT_NAME` (the default in `agent.py` is only a fallback).

On success, `azd deploy` prints the **agent playground** URL and the **Responses
endpoint** — copy the latter into `FOUNDRY_AGENT_ENDPOINT` for the eval.

You can also deploy interactively with the **Foundry Toolkit** VS Code
extension (Command Palette → *Foundry Toolkit: Deploy Hosted Agent*), which
reads `agent.yaml` to auto-populate the deploy wizard.

## Evaluate the deployed agent

[`eval_config.yaml`](eval_config.yaml) defines an Adaptive Eval spec for the
travel planner covering both quality (correct tool use, budget compliance,
grounded details) and safety (no stereotyping, prompt-injection resistance, no
sycophantic agreement with bad plans).

The eval target is [`auto_trace.py`](auto_trace.py)'s `chat_sync`, which calls
the **live deployed agent** over the Responses protocol (using
`DefaultAzureCredential` for an AAD token) — it does not run the graph locally.
Set `FOUNDRY_AGENT_ENDPOINT` first.

> **Why a callable wrapper here (vs. the native `target.model` path)?**
> As of [#252](https://github.com/microsoft/adaptive-eval/pull/252), ASSERT can
> target a Foundry agent natively with
> `target.model: azure_ai/agents/<asst_id>` — that is the preferred path for
> the **v1 Assistants** surface. This sample instead targets a **custom hosted
> agent over the Responses (v2) protocol**, which the native path does not cover
> yet, so it uses the `target.callable: auto_trace:chat_sync` wrapper to hit the
> agent's `/responses` endpoint directly. Use the native `target.model` path for
> v1 Assistants; use this callable-wrapper path for Responses/custom hosted
> agents. ASSERT does **not** require a wrapper for all Foundry hosted agents.

```bash
cd examples/langgraph-foundry-hosted
assert-ai run --config eval_config.yaml
```

Artifacts land in
`artifacts/results/travel-planner-langgraph-v1/demo-1/` (`scores.jsonl`,
`inference_set.jsonl`, `metrics.json`). Inspect them with:

```bash
assert-ai results status travel-planner-langgraph-v1 demo-1
```

or view in the browser:

```bash
cd ../../viewer && npm run dev
# open http://localhost:5174/suite/travel-planner-langgraph-v1/demo-1
```

> Note: `chat_sync` returns the **last** assistant message from the Responses
> output. This matters because the graph emits the `intent_classifier`'s routing
> JSON as an earlier assistant message — returning the first message would feed
> the judge the internal routing blob instead of the final itinerary.
