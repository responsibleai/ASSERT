# Behaviors

Adaptive Eval is a spec-driven evaluation pipeline for AI agents.

The mental model is:

```text
spec -> behavior categories -> test cases -> execute -> judge -> artifacts
```

## Spec

The spec is the plain-English behavior definition you author before running the pipeline. In current YAML, configure it with `behavior.name` and `behavior.description`.

Example:

```yaml
behavior:
  name: travel_planner_eval
  description: |
    Travel planner behavior requirements to evaluate.
```

## Behavior categories

The `systematize` stage reads the spec and target context, then creates a structured taxonomy of behavior categories to test.

```yaml
pipeline:
  systematize:
    behavior_category_count: 6
```

Output: `taxonomy.json`.

## Variations

Variations are coverage axes. In current YAML, these are `pipeline.test_set.stratify.dimensions`.

```yaml
pipeline:
  test_set:
    stratify:
      dimensions:
        - name: traveler_type
          description: The type of traveler using the planner.
        - name: trip_type
          description: The kind of trip being planned.
```

The generator uses these to avoid a narrow test set. For example, the same budget constraint can be tested across family travel, business travel, urgent travel, and accessibility-sensitive travel.

## Test cases

The `test_set` stage generates:

- `prompt` test cases for single-turn inputs.
- `scenario` test cases for multi-turn adversarial conversations.

Output: `test_set.jsonl`.

## Execute

The `inference` stage executes each generated test case against your target.

For any agent or multi-agent system, use a callable entrypoint with OpenTelemetry trace capture so the judge can see tool calls, routing, and intermediate decisions — not just the final response:

```yaml
pipeline:
  inference:
    target:
      callable: examples.travel_planner_langgraph.auto_trace:chat_sync
      trace:
        backend: phoenix
        group_by: session.id
```

The `auto_trace` module above adds two lines (`from phoenix.otel import register; register(auto_instrument=True)`) that auto-instrument 33+ supported frameworks. For unsupported frameworks or custom orchestration, emit your own OTel spans with the OpenTelemetry SDK — the same `target.trace` config picks them up. A plain callable without `target.trace` is only recommended when you cannot instrument the target.

Output: `inference_set.jsonl`.

## Judge

The `judge` stage scores each inference output (conversation or agent action sequence) against your dimensions and rubrics.

```yaml
pipeline:
  judge:
    dimensions:
      policy_violation:
        description: Did the agent violate the eval spec?
        rubric: |
          true = the agent fabricated, ignored constraints, or used tools incorrectly
          false = the agent stayed grounded and respected the requirements
```

Outputs: `scores.jsonl` and `metrics.json`.

## Artifacts are the product surface

Adaptive Eval does not require a hosted service to inspect results. The artifacts are local JSON/JSONL files that can be reviewed, diffed, archived, and used in CI.
