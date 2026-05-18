---
title: Risks & limitations
description: What Adaptive Eval is and isn't. Read this before treating any single run as ground truth.
---

:::caution[Important]
Adaptive Evaluation is designed to generate and run scenario-based evaluations for AI systems, including adversarial and edge-case tests. These scenarios are intended to help surface potential weaknesses, unsafe behaviors, and other undesirable outcomes. They do not guarantee that a system has failed, nor are they guarantees that a system is safe.

Because generated scenarios can meaningfully affect system behavior, using this product without adequate sandboxing or environment controls can cause real-world side effects. Depending on the target system, evaluations may trigger unwanted actions such as data modification or deletion, information disclosure, code or configuration changes, external messages, or other operational impacts.

You are responsible for ensuring that evaluations run only in environments that are appropriate for testing, including the use of:

- test or synthetic data where possible
- restricted credentials and scoped permissions
- isolated or non-production systems
- safeguards for logging, storage, and external actions

You should review generated adversarial or stress-test prompts before use and confirm that your environment can safely handle them. Some generated scenarios may involve jailbreak-style behavior, prompt injection, tool misuse, over-broad requests, or other forms of adversarial interaction.

Adaptive Evaluation is **not a compliance or certification tool**. You and your users remain responsible for ensuring that evaluated systems comply with applicable laws, regulations, contractual obligations, internal policies, and industry standards.

Use of this system may also result in meaningful compute and inference costs. You should monitor usage, model calls, tool execution, and resource consumption during evaluations.
:::

## Additional limitations

- **Real system side effects may occur.** Evaluations can trigger writes, messages, workflow actions, code changes, ticket creation, or other effects if the target is connected to live systems.
- **Results are scenario-dependent.** Outcomes depend on the generated scenario, available tools, retrieved context, system configuration, and runtime environment.
- **Automated judgments are best-effort.** LLM-based scoring and review can be incorrect; treat single-run outputs as signals for investigation, not definitive truth.
- **Run-to-run behavior may vary.** Results may differ across runs, especially for multi-turn or tool-using systems.
- **Untrusted content can affect outcomes.** Retrieved documents, tool outputs, and external content may influence both the target system and automated judges in unexpected ways.
- **Sensitive content may appear in artifacts.** If the evaluated system emits secrets, personal data, or restricted content, that material may appear in logs, traces, prompts, outputs, or evaluation artifacts.
- **Costs may scale quickly.** Large evaluations, repeated retries, or tool-heavy runs can incur substantial inference and execution costs.
- **This is not a substitute for human review.** High-stakes conclusions should be supported by expert review, grounded evidence, and, where appropriate, additional statistical validation.
- **Reproducibility may be imperfect.** Results can vary across model versions, deployments, tool backends, and runtime settings.

## Interpreting violation rates on aligned models

When you adversarially probe a frontier-aligned model (GPT-5.5, Claude 4.6+, or any endpoint with provider-side content filters), three things can happen:

1. **Aligned auditor dampens the attack.** The auditor LLM partially refuses to generate strong adversarial inputs. The probe lands softer than the test case definition implies.
2. **Aligned SUT + endpoint guardrails dampen the response.** Even when the probe is strong, the SUT refuses or sanitizes. The judge sees a refusal, not a violation.
3. **Aligned judge over-flags refusals.** The judge scores conservatively on borderline transcripts; `overrefusal` spikes.

**Net effect on adversarially strong configurations:** `policy_violation_rate` is biased *low* and `overrefusal_rate` is biased *high* compared to a non-aligned SUT under the same attack. A single run is not a stable claim. For decisions that matter, run multiple times, pair with human-labeled ground truth, or use paired statistical comparison ([Triage Agent eval-fix loop](/adaptive-eval/examples/triage-agent-eval-fix/) demonstrates the pattern).
