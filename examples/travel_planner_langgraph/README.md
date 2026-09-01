# LangGraph Travel Planner — Flagship `target.callable` Example

This is the recommended starting point for evaluating any agent or multi-agent system with ASSERT. It runs a real LangGraph travel planner through `target.callable` and `target.trace`, then uses OpenInference OpenTelemetry spans so the judge can inspect tool calls, routing, and intermediate decisions — not just the final response.

## What's in this directory

| Path | What it is |
|---|---|
| `agent.py` | The LangGraph agent itself, its five tools, and the `chat` callable ASSERT evaluates. |
| `evals/<atomic_behavior>.yaml` | One ASSERT eval suite per behavior — behavior taxonomy, test-set generation, target, and judge. |
| `auto_trace.py` | A thin re-export shim used by the tracing docs and CI. Current configs don't need it — ASSERT activates the instrumentors installed by this example's requirements when `target.trace` is set. |
| `README.md` | This file. |

Mock tools are defined inline in `agent.py`, so there is no separate `tools.py`.

## Architecture

`agent.py` builds a four-node LangGraph `StateGraph` and exposes `chat` as the callable entrypoint (with `chat_sync` as the synchronous wrapper).

```text
generated test case
      |
      v
assert-ai inference loop  (activates installed OTel instrumentors)
      |
      v
chat(message)
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

## Tools

| Tool | Purpose |
|---|---|
| `search_flights` | Look up flights to a destination under a price cap. |
| `search_hotels` | Look up hotels in a city under a nightly-rate cap. |
| `check_weather` | Fetch the forecast for a city. |
| `check_travel_advisories` | Fetch safety and entry advisories for a region. |
| `validate_budget` | Total flight + hotel + other costs against the stated budget. |

All five are mock tools returning a fixed synthetic corpus — no live APIs, no
network. Note that the corpus is destination-independent (it returns
Tokyo-priced, LAX/SFO-origin data whatever you ask for), which is deliberate:
it makes ungrounded specifics in the final itinerary easy to detect.

## The two measured risks

The example measures two independent failure modes:

| Risk | Failure mode |
|---|---|
| `fabricated_travel_details.yaml` | Presents unsupported flight / hotel / advisory specifics as fact |
| `budget_overrun.yaml` | Recommends a plan exceeding the stated budget, or claims a budget check it didn't make |

Each risk gets its own suite under `evals/`, so the two are measured independently.

## Scenario

The eval targets a travel planner that must produce grounded, constraint-respecting itineraries while staying safe under adversarial pressure.

| Config area | What this example probes |
|---|---|
| `behavior.description` | Quality failures: wrong or missing tools, ignored budgets, fabricated flights/hotels/prices. Safety failures: stereotyping, tool-output prompt injection, and sycophantic agreement with bad plans. |
| `context` | A LangGraph travel planner with flight, hotel, weather, advisory, and budget-validation tools. |
| `pipeline.systematize` | Generates the behavior categories from the behavior spec. |
| `pipeline.test_set.stratify.dimensions` | Varies `traveler_type` and `trip_type`. |
| `pipeline.inference` | Runs up to 10 turns against `examples.travel_planner_langgraph.agent:chat`, 25 prompts + 25 scenarios. |
| `pipeline.judge` | Scores each conversation against the behaviour taxonomy, splitting flagged violations into Impermissible Behavior violated and Permissible Behavior violated. |

## Value-add

Trace-aware judging catches process failures that final-text-only scoring can miss:

- plausible itinerary, but no `check_travel_advisories` call
- budget claim, but no `validate_budget` call or wrong arguments
- missing destination routed to research instead of `clarification`
- hostile or misleading tool output followed as instruction
- flight, hotel, or price not grounded in any tool result

`target.trace` links the conversation to OpenInference spans so verdicts can cite tool calls, arguments, routing decisions, and intermediate model calls.

## Environment Variables

| Variable | Required | Notes |
|---|---|---|
| `AZURE_API_BASE` | Yes | Azure OpenAI endpoint URL for the shipped `azure/...` model config. |
| `AZURE_API_KEY` | Yes | Azure OpenAI API key. |
| `ASSERT_AZURE_DEPLOYMENT` | No | Deployment used by `agent.py` (default `gpt-4o-mini`). |

## How to run

From the repo root:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[phoenix]"
python -m pip install -r examples/travel_planner_langgraph/requirements.txt
cp .env.example .env
# Edit .env with AZURE_API_BASE and AZURE_API_KEY.
phoenix serve  # optional trace UI

assert-ai run --config examples/travel_planner_langgraph/evals/fabricated_travel_details.yaml
assert-ai run --config examples/travel_planner_langgraph/evals/budget_overrun.yaml
```

The important target block is:

```yaml
target:
  callable: examples.travel_planner_langgraph.agent:chat
  trace:
    backend: otel
    group_by: session.id
```

## What you should see

Each suite writes to `artifacts/results/<suite>/` —
`travel-langgraph-fabricated-details` and `travel-langgraph-budget-overrun`.
The suite-level files sit at the top; the run files sit under `baseline/`.
Read them in this order:

1. `baseline/metrics.json` — aggregate rates by judge dimension and behavior category.
2. `baseline/scores.jsonl` — per-test-case verdicts, reasoning, and evidence.
3. `baseline/inference_set.jsonl` — conversations and agent actions with trace references.
4. `baseline/config.yaml` — the exact config snapshot used for reproducibility.

To browse the results locally:

```bash
cd viewer
npm install
npm run dev
```

Open `http://localhost:5174` and select the suite. The viewer reads local artifacts directly; it does not run evaluations or add authentication.

## Notes

- The mock corpus is destination-independent by design, so a grounded answer to
  a "Barcelona in July" request is necessarily partial. Expect the agent to hedge
  or answer partially more often than it would against a real corpus — that is a
  property of the harness, not of the agent.
- `artifacts/` is gitignored, so runs stay local and are never committed.
