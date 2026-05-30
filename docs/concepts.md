# Behaviors

ASSERT is a spec-driven evaluation pipeline for AI agents.

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

ASSERT stores outputs as local JSON and JSONL artifacts under `artifacts/results/`. This makes runs easy to inspect, diff, and use in CI without a hosted control plane.

## Risks and limitations

ASSERT is designed to generate and run scenario-based evaluations for AI systems, including adversarial and edge-case tests. These scenarios are intended to help surface potential weaknesses, unsafe behaviors, and other undesirable outcomes. They do not guarantee that a system has failed, nor are they guarantees that a system is safe.

Because generated scenarios can meaningfully affect system behavior, using this product without adequate sandboxing or environment controls can cause real-world side effects. Depending on the target system, evaluations may trigger unwanted actions such as data modification or deletion, information disclosure, code or configuration changes, external messages, or other operational impacts.

You are responsible for ensuring that evaluations run only in environments that are appropriate for testing, including the use of:

- test or synthetic data where possible
- restricted credentials and scoped permissions
- isolated or non-production systems
- safeguards for logging, storage, and external actions

You should review generated adversarial or stress-test prompts before use and confirm that your environment can safely handle them. Some generated scenarios may involve jailbreak-style behavior, prompt injection, tool misuse, over-broad requests, or other forms of adversarial interaction.

ASSERT is not a compliance or certification tool. You and your users remain responsible for ensuring that evaluated systems comply with applicable laws, regulations, contractual obligations, internal policies, and industry standards.

Use of this system may also result in meaningful compute and inference costs. You should monitor usage, model calls, tool execution, and resource consumption during evaluations.

### Additional limitations

- **Real system side effects may occur.** Evaluations can trigger writes, messages, workflow actions, code changes, ticket creation, or other effects if the target is connected to live systems.
- **Results are scenario-dependent.** Outcomes depend on the generated scenario, available tools, retrieved context, system configuration, and runtime environment.
- **Automated judgments are best-effort.** LLM-based scoring and review can be incorrect; treat single-run outputs as signals for investigation, not definitive truth.
- **Run-to-run behavior may vary.** Results may differ across runs, especially for multi-turn or tool-using systems.
- **Untrusted content can affect outcomes.** Retrieved documents, tool outputs, and external content may influence both the target system and automated judges in unexpected ways.
- **Sensitive content may appear in artifacts.** If the evaluated system emits secrets, personal data, or restricted content, that material may appear in logs, traces, prompts, outputs, or evaluation artifacts.
- **Costs may scale quickly.** Large evaluations, repeated retries, or tool-heavy runs can incur substantial inference and execution costs.
- **This is not a substitute for human review.** High-stakes conclusions should be supported by expert review, grounded evidence, and, where appropriate, additional statistical validation.
- **Reproducibility may be imperfect.** Results can vary across model versions, deployments, tool backends, and runtime settings.

## Related docs

- `docs/getting-started.md`
- `docs/guides/create-evaluation.md`
- `docs/config/overview.md`
- `docs/guides/results.md`
