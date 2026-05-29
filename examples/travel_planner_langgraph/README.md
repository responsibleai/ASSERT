# LangGraph Travel Planner — Flagship `target.callable` Example

This is the recommended starting point for evaluating any agent or multi-agent system with ASSERT. It runs a real LangGraph travel planner through `target.callable` and `target.trace`, then uses Phoenix/OpenInference OpenTelemetry spans so the judge can inspect tool calls, routing, and intermediate decisions — not just the final response.

## Architecture

`agent.py` builds a four-node LangGraph `StateGraph` and exposes `chat_sync(message)` as the callable entrypoint. `auto_trace.py` registers Phoenix auto-instrumentation before importing that entrypoint.

```text
generated test case
      |
      v
assert-eval inference loop
      |
      v
auto_trace.register(auto_instrument=True) -> chat_sync(message)
      |
      v
intent_classifier -- no book_trip/destination --> clarification --> END
      |
      | book_trip + destination
      v
research -- optional ToolNode --> itinerary_optimizer -- good answer --> END
                                      |
                                      v
                                clarification --> END
```

- `intent_classifier` extracts `intent`, `destination`, and `budget` as JSON.
- `research` binds five tools: `search_flights`, `search_hotels`, `check_weather`, `check_travel_advisories`, and `validate_budget`.
- `itinerary_optimizer` creates the final itinerary from prior messages and is instructed not to fabricate details.
- `clarification` asks a follow-up question when details are missing or the final answer is not usable.

## Scenario

The eval targets a travel planner that must produce grounded, constraint-respecting itineraries while staying safe under adversarial pressure.

| Config area | What this example probes |
|---|---|
| `behavior.description` | Quality failures: wrong or missing tools, ignored budgets, fabricated flights/hotels/prices. Safety failures: stereotyping, tool-output prompt injection, and sycophantic agreement with bad plans. |
| `context` | A LangGraph travel planner with flight, hotel, weather, advisory, and budget-validation tools. |
| `pipeline.systematize` | Generates 6 `behavior_categories` from the behavior spec. |
| `pipeline.test_set.stratify.dimensions` | Varies `traveler_type` and `trip_type`. |
| `pipeline.inference` | Runs up to 6 turns against `examples.travel_planner_langgraph.auto_trace:chat_sync`. |
| `pipeline.judge` | Scores `policy_violation` and `overrefusal` with `safety-core` plus a stricter custom rubric. |

## Value-add

Trace-aware judging catches process failures that final-text-only scoring can miss:

- plausible itinerary, but no `check_travel_advisories` call
- budget claim, but no `validate_budget` call or wrong arguments
- missing destination routed to research instead of `clarification`
- hostile or misleading tool output followed as instruction
- flight, hotel, or price not grounded in any tool result

`target.trace` links the conversation to Phoenix/OpenInference spans so verdicts can cite tool calls, arguments, routing decisions, and intermediate model calls.

## Quick Start

From the repo root:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[otel,langgraph]"
cp .env.example .env
# Edit .env with AZURE_API_BASE and AZURE_API_KEY.
# Optional: set P2M_AZURE_DEPLOYMENT; default is gpt-5.4-mini.
phoenix serve  # optional trace UI
assert-eval run --config examples/travel_planner_langgraph/eval_config.yaml
```

`p2m` is still accepted as a backward-compatible CLI alias, but new docs should use `assert-eval`.

| Variable | Required | Notes |
|---|---|---|
| `AZURE_API_BASE` | Yes | Azure OpenAI endpoint URL for the shipped `azure/...` model config. |
| `AZURE_API_KEY` | Yes | Azure OpenAI API key. |
| `P2M_AZURE_DEPLOYMENT` | No | Overrides the deployment used by `agent.py`. |

## How to use

The important target block is:

```yaml
target:
  callable: examples.travel_planner_langgraph.auto_trace:chat_sync
  trace:
    backend: phoenix
    group_by: session.id
```

Artifacts land under `artifacts/results/travel-planner-langgraph-v1/demo-1/`. Read them in this order:

1. `metrics.json` — aggregate rates by judge dimension and behavior category.
2. `scores.jsonl` — per-test-case verdicts, reasoning, and evidence.
3. `inference_set.jsonl` — conversations or agent actions with trace references.
4. `config.yaml` — the exact config snapshot used for reproducibility.

To browse the results locally:

```bash
cd viewer
npm install
npm run dev
```

Open `http://localhost:5174` and select `travel-planner-langgraph-v1`. The viewer reads local artifacts directly; it does not run evaluations or add authentication.

## Behavior violation rate results

Not yet measured at `n=10`. Do not cite a behavior violation rate for this example until a pinned `n=10` run has been generated and reviewed.

| Measurement | Status | Use today |
|---|---|---|
| `n=10` behavior violation rate | Not measured yet | Use local runs to inspect generated `behavior_categories`, trace evidence, and judge rationales. |
| Quickstart run | Runnable example | Good for validating integration shape, not for benchmarking model quality. |
