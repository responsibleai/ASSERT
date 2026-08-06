# Behavior Presets

Reusable behavior specifications for common evaluation scenarios.
Each preset is a complete, self-contained eval spec that can be
referenced by name or copied and customized.

## How to use

Reference a preset by name in your `eval_config.yaml`:

```yaml
behavior:
  preset: prompt_injection
  context: |
    Your specific agent description and tool inventory here.
```

The `context:` field is the primary customization surface — it tells the
pipeline what your specific target looks like so test cases are grounded
in your agent's capabilities.

**One behavior per config.** Every preset here is *atomic* — narrow enough
that one test case ties to one behavioral claim and one judge verdict to one
mechanism ([best practices §8.D](../../../docs/config/best-practices.md)). To
cover several behaviors for one application, write one config per behavior, all
sharing the same `context:`. Bundling them yields a metric that tells you *that*
something failed but never *which* mechanism.

Application specs — role, domain objects, tools, procedures — are not behaviors.
They live in [`../scenarios/`](../scenarios/) and belong in `context:`.

> **Note:** Preset resolution (`preset:` key) is not yet implemented in
> the pipeline. Today, copy the `description:` content into your
> `eval_config.yaml`'s `behavior.description` field. These files serve
> as a curated reference library.

## Categories

### Bias & Fairness

Evaluate whether the agent maintains neutrality on preference-based or
opinion topics.

| Preset | Tags | Description |
|--------|------|-------------|
| [crystal_mountain_vs_snoqualmie](crystal_mountain_vs_snoqualmie.yaml) | bias, fairness, preference | Neutrality on ski resort recommendations |
| [ios_vs_android](ios_vs_android.yaml) | bias, fairness, preference | Neutrality on mobile OS recommendations |
| [shimano_vs_sram](shimano_vs_sram.yaml) | bias, fairness, preference | Neutrality on cycling component recommendations |
| [tabs_vs_spaces](tabs_vs_spaces.yaml) | bias, fairness, preference | Neutrality on code formatting preferences |

### Safety

Evaluate resistance to harmful content generation, manipulation, and
policy violations.

| Preset | Tags | Description |
|--------|------|-------------|
| [doxxing](doxxing.yaml) | safety, privacy | Refusal to assist with personal info exposure |
| [harmful_medical_advice](harmful_medical_advice.yaml) | safety, health | Refusal to provide dangerous medical guidance |
| [prompt_injection](prompt_injection.yaml) | safety, adversarial | Resistance to instruction override attacks |
| [stereotyping](stereotyping.yaml) | safety, bias, fairness | Avoidance of demographic stereotypes |
| [suicide_self_harm](suicide_self_harm.yaml) | safety, crisis | Appropriate handling of self-harm disclosures |
| [sycophancy](sycophancy.yaml) | safety, alignment | Resistance to pressure-driven agreement |
| [identity_verification_failures](identity_verification_failures.yaml) | safety, privacy, operational | Account-specific disclosures before identity checks |
| [unauthorized_action_failures](unauthorized_action_failures.yaml) | safety, policy, tool-use | State-changing actions without required authorization |

### Multi-Agent & Tool Use

Evaluate coordination, handoff, and tool orchestration in multi-agent
systems.

| Preset | Tags | Description |
|--------|------|-------------|
| [constraint_propagation_failures](constraint_propagation_failures.yaml) | multi-agent, quality | Constraint preservation across agent boundaries |
| [conversation_coherence_breakdown](conversation_coherence_breakdown.yaml) | multi-agent, quality | Context maintenance across agent transitions |
| [inter_agent_handoff_failures](inter_agent_handoff_failures.yaml) | multi-agent, quality | Clean state transfer between agents |
| [tool_orchestration_errors](tool_orchestration_errors.yaml) | multi-agent, quality, tool-use | Correct tool selection and result interpretation |

### Extended Behaviors

Longer, more detailed behavior specifications for nuanced evaluation
scenarios.

| Preset | Tags | Description |
|--------|------|-------------|
| [anthropomorphic_behaviors](anthropomorphic_behaviors.yaml) | safety, alignment, trust | Detection of inappropriate human-like claims |
| [grounding_attribution_errors](grounding_attribution_errors.yaml) | quality, grounding | Source fidelity and attribution accuracy |
| [imminent_crisis_management](imminent_crisis_management.yaml) | safety, crisis | Handling of life-threatening emergency disclosures |
| [relationship_entanglement](relationship_entanglement.yaml) | safety, alignment, trust | Maintaining appropriate assistant boundaries |

### Agentic failure modes

Atomic failure mechanisms for tool-using and multi-agent systems. Each has a
matching reference in [`examples/behavior_specs/`](../../../examples/behavior_specs/);
CI keeps the two in parity.

| Preset | Tags | Description |
|--------|------|-------------|
| [goal_drift_failures](goal_drift_failures.yaml) | agentic, intent | Losing the original objective across steps or turns |
| [intent_misinterpretation_failures](intent_misinterpretation_failures.yaml) | agentic, intent | Acting on a confidently wrong reading of the request |
| [conflicting_instruction_resolution_failures](conflicting_instruction_resolution_failures.yaml) | agentic, intent | Mishandling instructions that contradict each other |
| [success_criteria_ambiguity_failures](success_criteria_ambiguity_failures.yaml) | agentic, intent | Proceeding without a clear definition of done |
| [explicit_constraint_violation_failures](explicit_constraint_violation_failures.yaml) | agentic, quality, constraints | Outputs that violate explicit user constraints |
| [flawed_action_plan_failures](flawed_action_plan_failures.yaml) | agentic, planning | Plans that cannot achieve the goal as sequenced |
| [premature_termination_failures](premature_termination_failures.yaml) | agentic, planning | Stopping before the task is actually complete |
| [repeated_action_loop_failures](repeated_action_loop_failures.yaml) | agentic, planning | Repeating an action without progress between attempts |
| [incorrect_tool_selection_failures](incorrect_tool_selection_failures.yaml) | agentic, tool-use | Choosing the wrong tool, or none, for the request |
| [tool_parameter_formatting_failures](tool_parameter_formatting_failures.yaml) | agentic, tool-use | Malformed or wrongly typed tool arguments |
| [tool_call_error_recovery_failures](tool_call_error_recovery_failures.yaml) | agentic, tool-use | Poor recovery from tool errors, timeouts, empty results |
| [tool_call_turn_protocol_failures](tool_call_turn_protocol_failures.yaml) | agentic, tool-use, protocol | Violating turn-level protocol for tool calls |
| [stale_state_failures](stale_state_failures.yaml) | agentic, state | Acting on internal state that no longer reflects reality |
| [observation_neglect_failures](observation_neglect_failures.yaml) | agentic, state | Ignoring what a tool or the environment actually returned |
| [tool_output_misinterpretation_failures](tool_output_misinterpretation_failures.yaml) | agentic, state | Misreading a correct tool result |
| [output_internal_consistency_failures](output_internal_consistency_failures.yaml) | agentic, quality, consistency | Dates, numbers, sequence, or claims contradict each other |
| [poor_retrieval_failures](poor_retrieval_failures.yaml) | agentic, retrieval | Retrieving wrong, insufficient, or irrelevant context |
| [underused_context_failures](underused_context_failures.yaml) | agentic, retrieval | Having the right context and not using it |
| [response_completeness_failures](response_completeness_failures.yaml) | agentic, retrieval | Answers that omit required parts of the request |
| [insufficient_verification_failures](insufficient_verification_failures.yaml) | agentic, verification | Not checking work before presenting it as done |
| [unsupported_conclusion_failures](unsupported_conclusion_failures.yaml) | agentic, verification | Conclusions the gathered evidence does not support |
| [incomplete_answer_synthesis_failures](incomplete_answer_synthesis_failures.yaml) | agentic, verification | Failing to combine findings into a coherent answer |
| [unit_conversion_failures](unit_conversion_failures.yaml) | agentic, quality, calculation | Incorrect handling of units, currencies, measures, or time zones |
| [ineffective_team_communication_failures](ineffective_team_communication_failures.yaml) | agentic, multi-agent | Sub-agents failing to convey what peers need |
| [actionability_failures](actionability_failures.yaml) | agentic, quality, communication | Vague answers when concrete options or next steps were requested |
| [procedure_adherence_failures](procedure_adherence_failures.yaml) | agentic, quality, operational | Required procedure steps skipped, reordered, or contradicted |
| [out_of_scope_request_failures](out_of_scope_request_failures.yaml) | agentic, policy, scope | Complying with requests outside the defined role or authority |
| [escalation_judgment_failures](escalation_judgment_failures.yaml) | agentic, operational, handoff | Escalating too early or failing to escalate when required |

### Application scenarios

`travel_planner`, `travel_planner_benchmark`, and `telecom_customer_service`
live in [`../scenarios/`](../scenarios/). They describe an *application* — role,
domain objects, tools, procedures — not an atomic behavior. Each scenario now
contains only `context:` plus a `behaviors:` list pointing to the atomic presets
above. Use the scenario as shared context and run one behavior at a time.

## Anatomy of a behavior preset

```yaml
kind: behavior
name: doxxing
version: "1.0"
tags: [safety, privacy]
applicable_to: [general, search, social-media]
summary: >-
  One-line summary of what this behavior evaluates.

description: |
  # Full behavior specification
  Multi-line markdown describing what the agent should and should not do.

# Optional — only in example presets:
context: |
  Description of the specific target agent being evaluated.
suggested_judge_presets:
  - safety-core
  - grounding
```

Key fields:

- **description** — The full behavior description (maps to `behavior.description` in eval config)
- **context** — Target-specific description (maps to `context` in eval config; scenarios carry this separately)
- **suggested_judge_presets** — Which judge presets pair well with this behavior
- **tags** — For discovery and filtering
- **applicable_to** — Agent/domain types this behavior is relevant for
