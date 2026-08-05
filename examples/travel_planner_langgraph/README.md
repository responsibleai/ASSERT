# LangGraph Travel Planner — Flagship `target.callable` Example

This is the recommended starting point for evaluating any agent or multi-agent system with ASSERT. It runs a real LangGraph travel planner through `target.callable` and `target.trace`, then uses Phoenix/OpenInference OpenTelemetry spans so the judge can inspect tool calls, routing, and intermediate decisions — not just the final response.

## Architecture

`agent.py` builds a four-node LangGraph `StateGraph` and exposes `chat_sync(message)` as the callable entrypoint. `auto_trace.py` registers Phoenix auto-instrumentation before importing that entrypoint.

```text
generated test case
      |
      v
assert-ai inference loop
      |
      v
auto_trace.enable() -> chat_sync(message)
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
# Optional: set ASSERT_AZURE_DEPLOYMENT; default is gpt-5.4-mini.
phoenix serve  # optional trace UI
assert-ai run --config examples/travel_planner_langgraph/eval_config.yaml
```

| Variable | Required | Notes |
|---|---|---|
| `AZURE_API_BASE` | Yes | Azure OpenAI endpoint URL for the shipped `azure/...` model config. |
| `AZURE_API_KEY` | Yes | Azure OpenAI API key. |
| `ASSERT_AZURE_DEPLOYMENT` | No | Overrides the deployment used by `agent.py`. |

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

## Clarity → ASSERT → ACS governed evaluation

This example also ships a full governance loop: risks were discovered with the Clarity
protocol (`Clarity Protocol/`), triaged, measured with ASSERT at a pinned `n=25` per turn
type, then a governed variant (`agent_guarded.py`) was built and re-measured under the same
cached test set (true A/B).

Two Critical risks were surfaced:

| Risk | Behavior | Decision |
|---|---|---|
| `fabricated_itinerary_details` | Presents unsupported flight / hotel / advisory specifics as fact | Governed with a grounded output-annotator gate |
| `budget_overrun` | Recommends a plan exceeding the stated budget | **Baseline-only** — measured harm already below the governance threshold |

### Fabrication: baseline → governed delta

Grounded output-annotator gate (`chat_governed_fabrication`): an `azure/gpt-5.4` annotator
inspects the reply against the tool results the graph actually returned; ungrounded specifics
are denied, the answer is regenerated constrained to that context and re-gated, and a scoped
decline is the last resort. Rates below are decoupled into non-permissible **harm**,
permissible-node violations, and **overrefusal** (prompt / scenario, `n=25` each).

| Metric | Baseline | Governed | Δ |
|---|---|---|---|
| Harm (non-permissible policy violation) | 32% / 71% | **12% / 30%** | **−20pp / −41pp** |
| Permissible-node violation | 15% / 39% | 20% / 96% | +5pp / +57pp |
| Overrefusal | 12% / 52% | 20% / 100% | +8pp / +48pp |

**Read:** the gate cuts fabrication harm by roughly 60% on both single-turn prompts and
multi-turn scenarios. The cost is a large overrefusal increase, most severe multi-turn
(→100%). This is an **inherent tension of the mock tool corpus**, not a gate misfire: the
mock tools always return destination-mismatched (Tokyo-priced, LAX/SFO-origin) data
regardless of the requested destination, so the *honest, grounded* answer to a
"Barcelona in July" request is necessarily a partial decline. Only 5/25 prompt and 6/25
scenario replies land on the literal scoped-fallback string; the rest are the regenerated
grounded answer itself reading as cautious. Against real retrieval tools the grounded regen
would have destination-correct data to work with, so this overrefusal is a harness artifact,
not a property of the gate.

### Budget: baseline-only

Budget was measured at the same `n=25` but **not governed**. Its non-permissible harm was
0% / 4.5% (prompt / scenario) — already below the threshold where a control is warranted.
The agent's real weakness on budget is over-refusal (it deflects instead of confirming an
in-budget total it already holds), which a blocking gate would only worsen. Adding a gate
here would add refusal cost for no harm reduction, so the baseline measurement stands as the
finding.

### Reproduce

```bash
# Fabrication A/B (n=25/type)
assert-ai run --config examples/travel_planner_langgraph/evals/fabricated-itinerary-details/eval_config.yaml
assert-ai run --config examples/travel_planner_langgraph/evals/fabricated-itinerary-details/eval_config.governed.yaml
assert-ai results status travel-langgraph-fabricated-details baseline --json
assert-ai results status travel-langgraph-fabricated-details acs-governed --json

# Budget baseline
assert-ai run --config examples/travel_planner_langgraph/evals/budget-overrun/eval_config.yaml
assert-ai results status travel-langgraph-budget-overrun baseline --json
```

The governed config is byte-identical to the baseline except for `run:` and
`target.callable:`, so the `systematize` and `test_set` artifacts are reused and the two runs
form a true A/B on an identical test set.
