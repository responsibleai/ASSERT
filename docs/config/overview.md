# Config Overview

ASSERT uses one YAML file (commonly `eval_config.yaml`) to define what to test and how to run the pipeline.

## Mental model

Your config declares:

1. Behavior spec (`behavior.name`, `behavior.description`)
2. Target/system context (`context`)
3. Pipeline stages (`pipeline.systematize`, `pipeline.test_set`, `pipeline.inference`, `pipeline.judge`)

Pipeline execution is fixed order:

```text
systematize -> test_set -> inference -> judge
```

## Top-level sections

- `suite`: suite id for shared artifacts
- `run`: run id under suite
- `behavior`: evaluation behavior name and description
- `context`: system and constraints description
- `default_model`: optional stage model fallback
- `pipeline`: stage configuration

## Minimal example

```yaml
suite: support-agent-v1
run: run-1
behavior:
  name: support_quality
  description: |
    Evaluate policy adherence and grounding behavior.
context: |
  Customer support agent with order and refund tools.
pipeline:
  systematize:
    model:
      name: azure/gpt-5.4-mini
  test_set:
    prompt:
      sample_size: 40
    scenario:
      sample_size: 20
    model:
      name: azure/gpt-5.4-mini
  inference:
    target:
      callable: my_package.agent:chat_sync
      trace:
        backend: phoenix
        group_by: session.id
  judge:
    model:
      name: azure/gpt-5.4-mini
    dimensions:
      policy_violation:
        description: Did the target violate requirements?
        rubric: |
          true = violation observed
          false = no violation observed
```

## Where to go next

- Full schema details: `docs/config/schema.md`
- Authoring guidance: `docs/guides/create-evaluation.md`
- Official field reference: `CONFIG_REFERENCE.md`
