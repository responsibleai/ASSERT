# Travel Planner — NeurOSan Pattern

Demonstrates that **any custom agent orchestration** — no framework required — can
produce OTel traces that ASSERT's evaluation pipeline understands.

This is the NeurOSan-pattern variant of the travel-planner agent. The flagship [`travel_planner_langgraph`](../travel_planner_langgraph/) example uses LangGraph and auto-instrumented spans; this one keeps the same eval but implements orchestration in `agent.py` as plain Python functions with manual OpenTelemetry spans.

## Why this matters

The `phoenix_auto_trace/` demos show the happy path: Phoenix auto-discovers frameworks
and instruments them with 2 lines. But what about custom orchestrators, in-house
frameworks, or agents that Phoenix doesn't auto-instrument?

This demo proves the general case: if your code emits OpenTelemetry spans following
[OpenInference conventions](https://arize-ai.github.io/openinference/), ASSERT can
evaluate it — no adapter, no framework lock-in.

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

## Scenario

The eval targets a travel-planning assistant that must use tools, respect explicit user constraints, and produce grounded itineraries.
It generates six `behavior_categories`, stratifies by `traveler_type` and `trip_type`, then executes single-turn prompts and multi-turn scenarios through the callable target.

- `target.callable`: `examples.travel_planner_neurosan.agent:chat`
- `target.trace`: Phoenix trace capture grouped by `session.id`
- `max_turns`: 6, so scenario tests can probe follow-up behavior

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
assert-eval run --config examples/travel_planner_neurosan/eval_config.yaml
```

There is no separate NeurOSan extra in `pyproject.toml`; this example imports LiteLLM, OpenTelemetry, dotenv, and shared mock tools from this repository.
Required env vars are `AZURE_API_BASE` and `AZURE_API_KEY`; set `P2M_TARGET_MODEL` only if the target agent should use a different LiteLLM model than `azure/gpt-5.4-mini`.

## How to use

After a run, inspect the suite and run artifacts:

```bash
assert-eval results status travel-planner-neurosan-v1 custom-otel
cd viewer
npm install
npm run dev
# Open http://localhost:5174 and select travel-planner-neurosan-v1 / custom-otel.
```

Key files:

- `artifacts/results/travel-planner-neurosan-v1/taxonomy.json` — generated behavior categories
- `artifacts/results/travel-planner-neurosan-v1/test_set.jsonl` — generated test cases
- `artifacts/results/travel-planner-neurosan-v1/custom-otel/inference_set.jsonl` — responses and trace references
- `artifacts/results/travel-planner-neurosan-v1/custom-otel/scores.jsonl` — per-test-case judge verdicts
- `artifacts/results/travel-planner-neurosan-v1/custom-otel/metrics.json` — behavior violation rates

## Behavior violation rate results

This README does not include a measured n=10 behavior violation rate yet. Run the eval, check `metrics.json`, and report the model, sample size, and run ID alongside any rate.
Do not compare this variant to LangGraph until both have the same config, model settings, and sample size.
