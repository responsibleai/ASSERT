# Custom-Instrumented Travel Planner (NeurOSan Pattern)

Demonstrates that **any custom agent orchestration** — no framework required — can
produce OTel traces that p2m's evaluation pipeline understands.

## Why this matters

The `phoenix_auto_trace/` demos show the happy path: Phoenix auto-discovers frameworks
and instruments them with 2 lines. But what about custom orchestrators, in-house
frameworks, or agents that Phoenix doesn't auto-instrument?

This demo proves the general case: if your code emits OpenTelemetry spans following
[OpenInference conventions](https://arize-ai.github.io/openinference/), p2m can
evaluate it — no adapter, no framework lock-in.

## Architecture

```
coordinator (CHAIN)
├── intent_classifier (AGENT)
│   └── intent_classifier.llm (LLM)
├── flight_searcher (AGENT)
│   ├── tool:search_flights (TOOL)
│   └── flight_searcher.llm (LLM)
├── hotel_searcher (AGENT)
│   ├── tool:search_hotels (TOOL)
│   └── hotel_searcher.llm (LLM)
├── safety_advisor (AGENT)
│   ├── tool:check_weather (TOOL)
│   ├── tool:check_travel_advisories (TOOL)
│   └── safety_advisor.llm (LLM)
└── itinerary_optimizer (AGENT)
    ├── tool:validate_budget (TOOL)
    └── itinerary_optimizer.llm (LLM)
```

Each "agent" is a plain Python function. OTel spans are created manually with
`opentelemetry-api` — no Phoenix auto-instrumentor involved.

## Running

```bash
uv run p2m run --config examples/travel_planner_neurosan/eval_config.yaml
```

## What the judge sees

Same artifact schema as auto-instrumented demos: `inference_set.jsonl`, `scores.jsonl`,
`metrics.json`. The judge grades the same dimensions — it doesn't know or care whether
traces came from auto-instrumentation or manual spans.
