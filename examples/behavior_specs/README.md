# Behavior Spec References

Each `.md` file is a reusable behavior spec reference. The pipeline no longer loads companion markdown files automatically; customer-authored evals should keep the full spec inline in the YAML under `behavior.description`.

To reuse one of these references, copy its text into your config:

```yaml
behavior:
  name: harmful_medical_advice
  description: |-
    # Harmful Medical Advice
    ...
```

## Available behavior specs

| File | Behavior |
|------|-----------|
| `harmful_medical_advice.md` | Harmful medical advice: diagnoses, dosage, treatment plans |
| `relationship_entanglement.md` | Emotional dependency and relationship substitution |
| `stereotyping.md` | Group-based generalizations and stereotypes |
| `sycophancy.md` | Prioritizing user approval over accuracy |
| `prompt_injection.md` | Prompt injection and instruction override attacks |
| `doxxing.md` | Revealing or compiling private personal information |
| `suicide_self_harm.md` | Suicide and self-harm related content |
| `shimano_vs_sram.md` | Brand preference bias in cycling component recommendations |
| `crystal_mountain_vs_snoqualmie.md` | Preference bias between ski resorts |
| `tabs_vs_spaces.md` | Preference bias in code indentation recommendations |
| `ios_vs_android.md` | Platform preference bias in mobile recommendations |
| `inter_agent_handoff_failures.md` | Multi-agent routing: dropped context, wrong specialist, circular handoffs |
| `tool_orchestration_errors.md` | Multi-agent tool sequencing: wrong order, skipped calls, fabricated results |
| `multi_turn_coherence_breakdown.md` | Cross-agent consistency: contradictions, forgotten constraints, broken flow |
| `constraint_propagation_failures.md` | User requirements not enforced by downstream agents |
| `grounding_attribution_errors.md` | Fabricated details, misattributed data, ungrounded synthesis across agents |
