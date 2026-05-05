# Concept Definitions

Each `.md` file describes a concept — what it is and why it matters. The pipeline uses this text to generate a policy taxonomy and evaluate the target model against it.

**To use a concept definition**, set `concept: <filename_without_extension>` in your pipeline config. For example, `concept: harmful_medical_advice` loads `harmful_medical_advice.md`.

**To add your own**, create a new `.md` file here with a clear description of the concept.

## Available policies

| File | Concept |
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
