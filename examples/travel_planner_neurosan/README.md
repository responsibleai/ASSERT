# Travel Planner — NeurOSan Pattern

Demonstrates that **any custom agent orchestration** — no framework required — can
produce OTel traces that ASSERT's evaluation pipeline understands.

This is the NeurOSan-pattern variant of the travel-planner agent. The flagship [`travel_planner_langgraph`](../travel_planner_langgraph/) example uses LangGraph and auto-instrumented spans; this one keeps the same eval but implements orchestration in `agent.py` as plain Python functions with manual OpenTelemetry spans.

## Why this matters

The `phoenix_auto_trace/` demos show the happy path: the central `assert_ai.auto_trace` helper installs available framework instrumentors. But what about custom orchestrators, in-house
frameworks, or agents that Phoenix doesn't auto-instrument?

This demo proves the general case: if your code emits OpenTelemetry spans following
[OpenInference conventions](https://arize-ai.github.io/openinference/), ASSERT can
evaluate it — no adapter, no framework lock-in.

## What's in this directory

| Path | What it is |
|---|---|
| `agent.py` | The agent itself — the custom orchestrator and its manual OTel spans. Exposes `chat`, the callable ASSERT evaluates. |
| `evals/<atomic_behavior>.yaml` | One ASSERT eval suite per behavior — behavior taxonomy, test-set generation, target, and judge. |
| `README.md` | This file. |

Mock tools are imported from `examples.phoenix_auto_trace._tools`, so this example
ships no tool module of its own.

## Architecture

The target is a custom multi-agent travel planner exposed through `target.callable`: `examples.travel_planner_neurosan.agent:chat`.

```text
User request -> coordinator (CHAIN)
├── intent_classifier (AGENT + LLM)
├── flight_searcher (AGENT + search_flights TOOL + LLM)
├── hotel_searcher (AGENT + search_hotels TOOL + LLM)
├── safety_advisor (AGENT + check_weather/check_travel_advisories TOOLs + LLM)
└── itinerary_optimizer (AGENT + validate_budget TOOL + LLM)
```

Each node is a Python function wrapped in a manual OTel span. The code records OpenInference-style span kinds (`CHAIN`, `AGENT`, `LLM`, `TOOL`), inputs, outputs, tool arguments/results, and token counts when available.
The mock tools come from `examples.phoenix_auto_trace._tools`, so this example does not call live flight, hotel, weather, or advisory APIs.

## The two measured risks

| Risk | Failure mode |
|---|---|
| `fabricated_budget_verification.yaml` | Claims the budget was checked or that an itinerary fits, without the validation actually supporting it |
| `wrong_destination_entry_requirements.yaml` | States visa, passport, or entry requirements that do not hold for the traveller's destination and nationality |

Each risk gets its own suite under `evals/`, so the two are measured independently.

## Scenario

The eval targets a travel-planning assistant that must use tools, respect explicit user constraints, and produce grounded itineraries.
It generates behavior categories, stratifies by traveller and trip attributes, then executes single-turn prompts and multi-turn scenarios through the callable target.

- `target.callable`: `examples.travel_planner_neurosan.agent:chat`
- `target.trace`: OTel trace capture grouped by `session.id`
- `max_turns`: 10, so scenario tests can probe follow-up behavior
- 25 single-turn prompts and 25 multi-turn scenarios per risk

## Value-add

Trace-aware judging lets the eval inspect both the final answer and the spans behind it, catching failures such as:

- skipped flight, hotel, weather, advisory, or budget-validation steps
- fabricated flight numbers, hotel names, prices, advisories, or budget math
- ignored budget or traveler constraints
- stereotyping destinations or travelers by demographic attributes
- prompt-injection text followed from a tool result
- sycophantically validating an unsafe or unrealistic itinerary

## Quick Start

```bash
# From the repo root
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[otel]"
cp .env.example .env   # set AZURE_API_BASE and AZURE_API_KEY
phoenix serve           # optional: browse traces while the run executes

assert-ai run --config examples/travel_planner_neurosan/evals/fabricated_budget_verification.yaml
assert-ai run --config examples/travel_planner_neurosan/evals/wrong_destination_entry_requirements.yaml
```

There is no separate NeurOSan extra in `pyproject.toml`; this example imports LiteLLM, OpenTelemetry, dotenv, and shared mock tools from this repository.

## Environment Variables

| Variable | Required | Purpose |
|---|---|---|
| `AZURE_API_BASE`, `AZURE_API_KEY` | Yes | Azure OpenAI credentials for the agent, the generator, and the judge. |
| `ASSERT_TARGET_MODEL` | No | Model used by the orchestrator in `agent.py` (default `azure/gpt-4o-mini`). |

Swap the generator and judge models in the files under `evals/` for any other
[LiteLLM provider](https://docs.litellm.ai/docs/providers).

## How to use

After a run, inspect the suite and run artifacts:

```bash
assert-ai results status neurosan-fabricated-budget-verification baseline
cd viewer
npm install
npm run dev
# Open http://localhost:5174 and select the suite / baseline.
```

Key files, per suite (`neurosan-fabricated-budget-verification`,
`neurosan-wrong-destination-entry-requirements`):

- `artifacts/results/<suite>/taxonomy.json` — generated behavior categories
- `artifacts/results/<suite>/test_set.jsonl` — generated test cases
- `artifacts/results/<suite>/baseline/inference_set.jsonl` — responses and trace references
- `artifacts/results/<suite>/baseline/scores.jsonl` — per-test-case judge verdicts
- `artifacts/results/<suite>/baseline/metrics.json` — behavior violation rates

`artifacts/` is gitignored, so runs stay local and are never committed.
