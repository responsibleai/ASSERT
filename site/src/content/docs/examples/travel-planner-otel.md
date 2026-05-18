---
title: Travel Planner — OTel example
description: A LangGraph multi-tool agent evaluated through Phoenix / OpenInference auto-trace.
---

A LangGraph travel planner with five tools, evaluated end-to-end with two lines of OpenInference auto-instrumentation. This is the **recommended integration shape** for any framework agent.

## Source

```
examples/travel_planner_langgraph/
├── agent.py                  # the LangGraph agent (5 tools)
├── auto_trace.py             # 2-line Phoenix registration + re-export
├── travel_planner_eval.md    # spec (quality + safety)
└── eval_config.yaml          # pipeline config
```

## What you'll see

- **Spec-driven taxonomy.** ~15 failure modes derived from a plain-English spec — `fabricated_flight_price`, `skipped_budget_validation`, `dangerous_itinerary_agreement`, etc.
- **Trace-grounded verdicts.** The judge can see every tool call. A budget-validation skip surfaces even when the agent's final text claims "fits your budget."
- **Five factor dimensions** — `traveler_type`, `trip_type`, `urgency`, `tone`, `dietary_constraints` — that diversify test cases across realistic scenarios.

## Run it

```bash
python -m pip install -e ".[otel,langgraph]"
p2m run --config examples/travel_planner_langgraph/eval_config.yaml
```

## Read the flow

A turn-by-turn walkthrough of how the spec → taxonomy → test cases → execute → judge stages produce a specific verdict on a specific scenario: [Travel planner agent flow](/adaptive-eval/run/travel-planner-flow/).

## Why this integration shape

Same agent, three integration shapes, very different observability:

| Observable | OTel | `ModelResponse` callable | `str` callable |
|---|---|---|---|
| Final text | ✅ | ✅ | ✅ |
| Tool calls + arguments | ✅ | ✅ | ❌ |
| Tool results | ✅ | ❌ | ❌ |
| Routing decisions | ✅ | ❌ | ❌ |
| Latency breakdown | ✅ | ❌ | ❌ |

For agents with internal control flow, OTel is the only shape that gives the judge real visibility. See [Choose your target](/adaptive-eval/run/targets/).
