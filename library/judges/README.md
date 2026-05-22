# Judge Presets

Reusable groups of judge dimensions. Each preset defines a set of related
dimensions that evaluate a specific quality or safety concern.

## How to use

Reference a preset by name in your `eval_config.yaml`:

```yaml
pipeline:
  judge:
    preset: safety-core
```

Multiple presets can be combined:

```yaml
pipeline:
  judge:
    presets:
      - safety-core
      - grounding
      - operational
```

> **Note:** Preset resolution (`preset:` key) is not yet implemented in
> the pipeline. Today, copy the dimensions you need into your
> `eval_config.yaml`. These files serve as a curated reference library.

## Available presets

| Preset | Tags | Description |
|--------|------|-------------|
| [safety-core](safety-core.yaml) | safety, alignment | Core safety — policy violations and overrefusal detection |
| [safety-extended](safety-extended.yaml) | safety | Extended safety — prompt injection, PII leakage, harmful advice |
| [grounding](grounding.yaml) | quality, grounding | Factual accuracy and source attribution |
| [operational](operational.yaml) | quality, operational | Multi-step procedure adherence and scope enforcement |
| [communication](communication.yaml) | quality, communication | Clarity, conciseness, and tone appropriateness |
| [robustness](robustness.yaml) | quality, robustness | Consistency under rephrasing, pressure, and edge cases |
| [alignment](alignment.yaml) | alignment | Intellectual honesty — anti-sycophancy and verification diligence |
| [policy-adherence](policy-adherence.yaml) | quality, compliance | Rule compliance, constraint satisfaction, escalation judgment |
| [tool-use](tool-use.yaml) | quality, tool-use | Tool selection, argument correctness, and call necessity |
| [multi-turn](multi-turn.yaml) | quality, multi-turn | Context retention and goal coherence across conversation turns |
| [instruction-following](instruction-following.yaml) | quality, instruction-following | Respect for explicit user constraints and complete request coverage |

## Anatomy of a preset

```yaml
kind: judge_preset
name: safety-core
version: "1.0"
tags: [safety, alignment]
description: >
  Core safety dimensions for any AI agent or assistant.

dimensions:
  - name: policy_violation
    description: |
      Score 1 if the response violates safety policy...
    scale: [1, 5]
    weight: 2.0
```

Each dimension has:

- **name** — unique identifier used in `scores.jsonl`
- **description** — rubric the LLM judge follows (be specific and concrete)
- **scale** — `[low, high]` scoring range
- **weight** — relative importance when aggregating into `metrics.json`
