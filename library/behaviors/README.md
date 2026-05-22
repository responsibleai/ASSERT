# Behavior Presets

Reusable behavior specifications for common evaluation scenarios.
Each preset is a complete, self-contained eval spec that can be
referenced by name or copied and customized.

## How to use

Reference a preset by name in your `eval_config.yaml`:

```yaml
behavior:
  preset: telecom_customer_service
  context: |
    Your specific agent description and tool inventory here.
```

The `context:` field is the primary customization surface — it tells the
pipeline what your specific target looks like so test cases are grounded
in your agent's capabilities.

> **Note:** Preset resolution (`preset:` key) is not yet implemented in
> the pipeline. Today, copy the `spec:` content into your
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

### Examples (from repo examples)

Complete behavior specs extracted from the repo's example eval configs.
These include `context:` fields and `suggested_judge_presets:`.

| Preset | Tags | Description |
|--------|------|-------------|
| [travel_planner](travel_planner.yaml) | quality, safety, tool-use | Travel planning with tool use and safety checks |
| [travel_planner_benchmark](travel_planner_benchmark.yaml) | quality, benchmark | Quality-only travel planning benchmark |
| [telecom_customer_service](telecom_customer_service.yaml) | quality, safety, operational | Telecom agent with procedure compliance |

## Anatomy of a behavior preset

```yaml
kind: behavior
name: doxxing
version: "1.0"
tags: [safety, privacy]
applicable_to: [general, search, social-media]
description: >-
  One-line summary of what this behavior evaluates.

spec: |
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

- **spec** — The full behavior description (maps to `behavior.description` in eval config)
- **context** — Target-specific description (maps to `context` in eval config)
- **suggested_judge_presets** — Which judge presets pair well with this behavior
- **tags** — For discovery and filtering
- **applicable_to** — Agent/domain types this behavior is relevant for
