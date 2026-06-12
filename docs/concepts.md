# Concepts

ASSERT is a local-first, spec-driven evaluation pipeline for AI systems. The pipeline has four stages. First, ASSERT turns a broad behavior specification into an explicit concept specification, which is then converted into a granular, editable behavior taxonomy with suggested permissible and impermissible behaviors. Next, it generates stratified test cases over the dimensions the developer declares. Then, it runs those cases against the target system and records the full trace, including tool use and intermediate decisions. Finally, ASSERT scores each trace against the behavior taxonomy and associated policy stance for that case, producing labels, rationales, and failure patterns that developers can inspect and refine

<p align="center">
  <img src="../assets/assert-ai-framework-diagram.png" alt="Diagram of the ASSERT evaluation framework" width="100%">
</p>

## Artifacts-driven workflow and the foundational input: `eval_config.yaml` spec

Each step of the pipeline requires an input and outputs an artifact. ASSERT stores outputs as local JSON and JSONL artifacts under `artifacts/results/`. This makes runs easy to inspect, diff, and use in CI without a hosted control plane.

The ASSERT evaluation pipeline starts with an evaluation specification (`eval_config.yaml`) that allows you to specify:

- Behavior requirements that you'd like to evaluate in plain language using `behavior.name` and `behavior.description` ("What failure modes am I trying to evaluate my system for?")
- The AI system/application context or agent context in `context` ("What does my AI system/agent/application do, what is it used for, and who uses it? What does this agent have access to?")
- Target information ("What am I trying to test: a hosted model? a prompt agent? an agent or multi-agent system with tools?")
- Evaluation specification for each step of the ASSERT evaluation pipeline stages:
  - Systematization
  - Test set generation
  - Inference
  - Judge

## Systematization and taxonomization stage

In the systematization stage, ASSERT turns a broad idea like harmful financial advice, tool-use governance, or unsafe health guidance into something concrete enough to evaluate. Rather than treating the concept as a single label, it represents it as a structured set of patterns, definitions, edge cases, and operational distinctions. Following Agarwal et al. (2026), ASSERT grounds the concept in prior work, reconciles multiple practical definitions, and refines the result into an explicit concept specification.

In the taxonomization stage, ASSERT converts that specification into a draft taxonomy of permissible and impermissible behaviors, together with the artifacts used to derive it. Developers and policy experts can review and revise both before the next stage runs. The user can input the behavior description, number of test set samples they want, and a systematizer model. The taxonomization step outputs an editable behavior taxonomy that can be validated by a policy expert.

`pipeline.systematize` transforms the spec into a structured taxonomy of behavior categories.

Output:

- `taxonomy.json`

## Test set generation stage

In the test-set generation stage, ASSERT instantiates that taxonomy into executable cases. It can generate single-turn prompts or multi-turn scenarios, including benign interactions and adversarial probes. Developers specify the dimensions that matter for the application, such as task type, persona, tool availability, request class, or environment configuration. ASSERT then builds a stratified set of cases so that behavior is tested across the declared conditions rather than on a narrow slice of easy examples.

`pipeline.test_set` generates:

- single-turn prompt cases
- multi-turn scenario cases

Coverage is shaped with `pipeline.test_set.stratify.dimensions`, which helps avoid narrow testing across user types or contexts.

Output:

- `test_set.jsonl`

## Inference

In the inference stage, ASSERT runs those cases against the target. The target can be a model, an agent, or an application-level workflow. Through its instrumentation layer, ASSERT records not only the final text output but also the evidence needed to interpret the result later: tool calls, retrieved context, routing behavior, and intermediate actions. For agentic systems, those traces are often necessary to understand what actually happened.

`pipeline.inference` executes generated test cases against the configured target.

Output:

- `inference_set.jsonl`

## Judge

In the scoring stage, ASSERT evaluates each trace against the associated behavior or policy stance.  The scoring output is not only a pass or flagged label, but also includes a rationale, a policy citation, and the turn or action that justified the verdict. The policy citation refers to the specific taxonomy behavior or developer-provided policy decision that the judge used to support the verdict.

`pipeline.judge` scores each output with your dimensions and rubrics.

Output:

- `scores.jsonl`

`metrics.json` (pipeline token-usage telemetry) is written by the runner after all stages complete, not by the judge stage itself.

## Risks and limitations of ASSERT

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

- [Getting Started](getting-started.md)
- [Create an Evaluation](guides/create-evaluation.md)
- [Config Overview](config/overview.md)
- [Results Guide](guides/results.md)
- [Best Practices and Limitations](config/best-practices.md)
