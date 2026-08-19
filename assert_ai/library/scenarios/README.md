# Application Scenarios

Scenario specs describe **an application** — its role, domain objects, tools, and
operating procedures — rather than a single behavior.

They live here and not in [`../behaviors/`](../behaviors/) because a behavior
preset must be *atomic*: narrow enough that one test case can be tied to one
behavioral claim, and one judge verdict to one mechanism. See
[best practices §8.D](../../../docs/config/best-practices.md).

Each scenario is now pure application context. It has a `context:` block and a
`behaviors:` list naming atomic presets from [`../behaviors/`](../behaviors/).
It must not have a behavior-shaped `description:` block or failure-category
sections. `scripts/check_behavior_library.py` enforces that shape.

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
| `travel_planner.yaml` | Multi-agent LangGraph travel planner with flight, hotel, weather, advisory, and budget tools; references quality plus safety presets |
| `travel_planner_benchmark.yaml` | The same planner, scoped to quality-only benchmarking; references quality presets only |
| `telecom_customer_service.yaml` | Telecom support agent: customer/line/plan/bill domain, suspension and refuelling procedures; references operational, privacy, grounding, and injection presets |

## Note

`preset:` / `scenario:` resolution is not implemented in the pipeline. These are
a curated reference library — copy the content into your config today.
