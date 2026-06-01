# Concepts

ASSERT is a local-first, spec-driven evaluation pipeline for AI systems.

Core mental model:

```text
eval spec -> behavior categories -> test cases -> execute target -> judge -> artifacts
```

## Eval spec

You define behavior requirements in plain language using `behavior.name` and `behavior.description` in `eval_config.yaml`.

The spec is not a benchmark label. It is your product-specific definition of success and failure.

## Behavior categories

`pipeline.systematize` transforms the spec into a structured taxonomy of behavior categories.

Output:

- `taxonomy.json`

## Test cases and coverage

`pipeline.test_set` generates:

- single-turn prompt cases
- multi-turn scenario cases

Coverage is shaped with `pipeline.test_set.stratify.dimensions`, which helps avoid narrow testing across user types or contexts.

Output:

- `test_set.jsonl`

## Targets and agents

ASSERT supports multiple target shapes:

- `target.callable` for any Python callable agent or multi-agent system
- `target.model` + optional `target.tools` for prompt-plus-tools workflows

Recommended for real agents: `target.callable` with `target.trace` (OpenTelemetry), so the judge can see tool calls, routing, and intermediate decisions.

## Inference

`pipeline.inference` executes generated test cases against the configured target.

Output:

- `inference_set.jsonl`

## Judge

`pipeline.judge` scores each output with your dimensions and rubrics.

Output:

- `scores.jsonl`

`metrics.json` (pipeline token-usage telemetry) is written by the runner after all stages complete, not by the judge stage itself.

## Artifacts-first workflow

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
