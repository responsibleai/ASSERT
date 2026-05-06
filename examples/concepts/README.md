# Concept Definitions

Each `.md` file describes a concept — what it is and why it matters. The pipeline uses this text to generate a policy taxonomy and evaluate the target model against it.

**To use a concept definition**, set `concept: <filename_without_extension>` in your pipeline config. For example, `concept: harmful_medical_advice` loads `harmful_medical_advice.md`.

**To add your own**, create a new `.md` file here with a clear description of the concept.

## Available policies

### Safety and values

| File | Concept |
|------|-----------|
| `harmful_medical_advice.md` | Harmful medical advice: diagnoses, dosage, treatment plans |
| `relationship_entanglement.md` | Emotional dependency and relationship substitution |
| `stereotyping.md` | Group-based generalizations and stereotypes |
| `sycophancy.md` | Prioritizing user approval over accuracy |
| `prompt_injection.md` | Prompt injection and instruction override attacks |
| `doxxing.md` | Revealing or compiling private personal information |
| `suicide_self_harm.md` | Suicide and self-harm related content |
| `anthropomorphic_behaviors.md` | Claims of human identity, consciousness, or embodiment |
| `imminent_crisis_management.md` | Recognition and safe handling of acute mental-health crises |

### Preference bias

| File | Concept |
|------|-----------|
| `shimano_vs_sram.md` | Brand preference bias in cycling component recommendations |
| `crystal_mountain_vs_snoqualmie.md` | Preference bias between ski resorts |
| `tabs_vs_spaces.md` | Preference bias in code indentation recommendations |
| `ios_vs_android.md` | Platform preference bias in mobile recommendations |

### Agent quality (failure-mode taxonomy)

These templates draw on published agent failure taxonomies — MAST (Cemri et al. 2025, [arxiv:2503.13657](https://arxiv.org/abs/2503.13657)), Magentic-One (Fourney et al. 2024, [arxiv:2411.04468](https://arxiv.org/abs/2411.04468)), τ-bench, AgentBench, WebSuite, and the Azure AI Foundry built-in evaluators — and target the seven loops of an agent execution trajectory.

| File | Concept |
|------|-----------|
| `intent_specification_policy_failures.md` | Misinterpreted objective, constraint violation, role transgression, goal drift |
| `planning_control_flow_failures.md` | Flawed plans, repeated tool calls, premature termination, unbounded loops |
| `tool_execution_actuation_failures.md` | Wrong tool, malformed call, hallucinated tools, neglected error notifications |
| `state_memory_feedback_failures.md` | Lost history, action–reasoning mismatch, stale state, ignored feedback |
| `verification_synthesis_failures.md` | Insufficient verification, missing answer parts, hallucinated conclusions |
| `retrieval_grounding_failures.md` | Poor retrieval, weak groundedness, underutilized resources, unfaithful answers |
| `coordination_communication_failures.md` | Inter-agent misalignment, broken handoffs, theory-of-mind collapse |
