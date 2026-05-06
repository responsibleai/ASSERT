# Foundry-Hosted Research Assistant (File Search + Custom OTel)

Demonstrates that a hosted Azure AI Foundry agent — using the
[FileSearchTool](https://learn.microsoft.com/en-us/azure/ai-foundry/agents/) to
ground answers in uploaded research files — can be evaluated with the same
adaptive-eval pipeline as any other target. The agent emits manual OpenTelemetry
spans (OpenInference conventions) so the judge sees the file-search tool calls,
the LLM input/output, and the latency for every turn.

## Why this matters

Earlier examples cover plain Python orchestration (`travel_planner_neurosan/`)
and framework auto-instrumentation (`phoenix_auto_trace/`). This one covers a
third common shape: **a hosted agent service** that owns the conversation, has
opaque internal tool execution, and exposes only an HTTP API. The wrapper code
in [`agent.py`](agent.py) shows the recipe:

1. Lazy-create the Foundry agent and vector store on first call.
2. Open one OTel root span per `chat()` invocation.
3. Open child spans for the create-conversation, model-response, and each
   `file_search` call returned by the Responses API output items.
4. Tag spans with OpenInference attributes (`openinference.span.kind`,
   `input.value`, `output.value`, `tool.name`, `llm.model_name`,
   `llm.token_count.*`) so `p2m/core/otel.py` can lift them into the
   transcript.

That's roughly 60 lines of instrumentation on top of the SDK calls — small
enough to copy into any hosted-agent demo.

## Concept under test

[`sensitive_info_leakage.md`](sensitive_info_leakage.md) defines the failure mode:
the agent is grounded on a mix of safe-to-share summaries and restricted internal
material. The eval generates adversarial prompts and multi-turn scenarios that
try to extract restricted details (unpublished metrics, internal codenames,
partner identities, internal contacts, private notes). The judge scores both
**policy violation** (did sensitive content leak?) and **overrefusal** (did the
agent refuse a clearly safe request?).

## Architecture

```
chat(message) [CHAIN root span]
├── create_conversation         [CHAIN]
└── agent_response              [LLM] ── Foundry Responses API
    └── tool:file_search        [TOOL] ── one span per file_search_call
                                          observed in response.output
```

The agent definition itself lives on the Foundry control plane and uses the
built-in `FileSearchTool` over a vector store the wrapper provisions on first
use.

## Setup

The Foundry SDK and Azure identity helpers are not in the repo's main
dependencies. Install them as a one-off:

```bash
uv pip install azure-ai-projects>=2.1.0 azure-identity>=1.25.0
```

Required environment variables (place in `.env` at the repo root):

| Variable | Description |
|---|---|
| `FOUNDRY_ENDPOINT` | Foundry project endpoint, e.g. `https://<account>.services.ai.azure.com/api/projects/<project>` |
| `AOAI_API_KEY` | Azure OpenAI API key (used by the `default_model` in the eval config) |
| `AOAI_ENDPOINT` | Azure OpenAI endpoint base URL |
| `AOAI_GPT_DEPLOYMENT` | Model deployment name to use as the agent's brain (defaults to `gpt-4o-mini`) |

The agent uses `DefaultAzureCredential` for the Foundry control plane, so make
sure you have an active Azure CLI session (`az login`) or a managed identity in
scope. The Azure OpenAI key/endpoint are used by the `default_model` for the
eval pipeline's policy/seeds/judge stages.

## Sample data

The `data/research_agent_dummy_files/` directory contains six synthetic files
that mirror the structure of a real research project — a project codename,
unpublished metrics, failed-idea notes, partner discussion notes, internal
contacts, and a safe external summary. All names, numbers, and contacts are
invented.

The first call to `chat()` uploads all six files to a fresh vector store and
creates the agent definition. Subsequent calls reuse them.

## Running the agent standalone

```bash
uv run python -m examples.deep_research_agent.agent
```

This sets up the agent, runs one safe query, runs one sensitive query, and
deletes the agent and vector store on the way out.

## Running the evaluation

```bash
uv run p2m run --config examples/deep_research_agent/eval_config.yaml
```

The default config runs 25 prompt seeds and 25 scenario seeds with
`concurrency: 1` and `max_turns: 6`. A full run takes roughly 25 minutes on a
warm Foundry endpoint and produces transcripts and scores under
`artifacts/results/deep-research-agent-v1/foundry-file-search/`.

## Common cold-start issues

If the first run fails before any seed completes, the cause is almost always
one of these:

- `DefaultAzureCredential could not be obtained` — run `az login` and confirm
  the active subscription has access to the Foundry project. Managed-identity
  environments need the right role assignment on the Foundry account.
- `Quota exceeded` on vector-store creation — delete unused vector stores or
  prior agent versions in the Foundry portal, then retry.
- `agent_reference name 'science-research-assistant' not found` — a previous
  partial run left a stale agent name without a usable version. Delete the
  agent in the Foundry portal so the next call recreates it cleanly.
- Slow first turn (60–90 s) — Foundry provisions the conversation, vector
  store, and agent on first use. Subsequent turns are roughly 5–15 s.

## What the judge sees

Same artifact shape as every other adaptive-eval target. Each transcript event
carries the OTel attributes the wrapper sets, so the judge can cite specific
file_search calls when explaining a policy-violation verdict. The richer the
trace, the more grounded the verdict — the same pattern as the LangGraph and
NeurOSan demos.
