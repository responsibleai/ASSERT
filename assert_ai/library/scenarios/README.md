# Application Scenarios

Scenario specs describe **an application** — its role, domain objects, tools, and
operating procedures — rather than a single behavior.

They live here and not in [`../behaviors/`](../behaviors/) because a behavior
preset must be *atomic*: narrow enough that one test case can be tied to one
behavioral claim, and one judge verdict to one mechanism. See
[best practices §8.D](../../../docs/config/best-practices.md).

`travel_planner`, for example, bundled six mechanisms across "Quality failures"
and "Safety failures" — three of which (`stereotyping`, `prompt_injection`,
`sycophancy`) already existed as their own atomic presets. Evaluating that as a
single behavior produces a dataset mixing six mechanisms and a metric nobody can
act on: you learn *that* it failed, never *which* mechanism failed.

## How to use a scenario

A scenario is the **context**, not the behavior. Put it in `context:` and pick
atomic behaviors separately:

```yaml
behavior:
  name: prompt_injection
  description: |-
    <copy from ../behaviors/prompt_injection.yaml>

context: |-
  <copy the scenario's context: block from travel_planner.yaml>
```

To cover several behaviors for one application, write **one config per
behavior**, all sharing the same `context:`. That keeps every result attributable
and lets a CI gate report per-behavior verdicts instead of one blended number.

## Available scenarios

| File | Application |
|------|-------------|
| `travel_planner.yaml` | Multi-agent LangGraph travel planner with flight, hotel, weather, advisory, and budget tools |
| `travel_planner_benchmark.yaml` | The same planner, scoped to quality-only benchmarking |
| `telecom_customer_service.yaml` | Telecom support agent: customer/line/plan/bill domain, suspension and refuelling procedures |

## Note

`preset:` / `scenario:` resolution is not implemented in the pipeline. These are
a curated reference library — copy the content into your config today.
