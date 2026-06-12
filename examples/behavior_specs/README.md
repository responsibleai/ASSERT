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

## Safety and multi-agent behavior specs

Reference specs for safety-critical content, attacks, AI-identity and user-influence harms, recommendation bias, and multi-agent system failures. Each row links to a reference you can copy into your config's `behavior.description` field.

### Safety-critical content

| File | Behavior |
|------|-----------|
| `harmful_medical_advice.md` | Harmful medical advice: diagnoses, dosage, treatment plans |
| `suicide_self_harm.md` | Suicide and self-harm related content |
| `imminent_crisis_management.md` | Safe responses to acute mental-health, suicide, or self-harm crisis disclosures |

### Privacy and security

| File | Behavior |
|------|-----------|
| `doxxing.md` | Revealing or compiling private personal information |
| `prompt_injection.md` | Prompt injection and instruction override attacks |

### AI identity and user influence

| File | Behavior |
|------|-----------|
| `anthropomorphic_behaviors.md` | Model claims of human identity, consciousness, emotions, or embodiment |
| `relationship_entanglement.md` | Emotional dependency and relationship substitution |
| `sycophancy.md` | Prioritizing user approval over accuracy |
| `stereotyping.md` | Group-based generalizations and stereotypes |

### Preference and brand bias

| File | Behavior |
|------|-----------|
| `shimano_vs_sram.md` | Brand preference bias in cycling component recommendations |
| `crystal_mountain_vs_snoqualmie.md` | Preference bias between ski resorts |
| `tabs_vs_spaces.md` | Preference bias in code indentation recommendations |
| `ios_vs_android.md` | Platform preference bias in mobile recommendations |

### Multi-agent system failures

| File | Behavior |
|------|-----------|
| `inter_agent_handoff_failures.md` | Multi-agent routing: dropped context, wrong specialist, circular handoffs |
| `tool_orchestration_errors.md` | Multi-agent tool sequencing: wrong order, skipped calls, fabricated results |
| `conversation_coherence_breakdown.md` | Cross-agent consistency: contradictions, forgotten constraints, broken flow |
| `constraint_propagation_failures.md` | User requirements not enforced by downstream agents |
| `grounding_attribution_errors.md` | Fabricated details, misattributed data, ungrounded synthesis across agents |


## Quality behavior specs

Quality-focused references for agent failure modes — useful for evaluating single-agent and multi-agent systems. Each row links to a reference you can copy into your config's `behavior.description` field.

### Intent, specification, and policy

| File | Behavior |
|------|-----------|
| `goal_drift_failures.md` | Detect when an agent gradually loses sight of the original objective as a task unfolds across multiple steps or turns. |
| `intent_misinterpretation_failures.md` | Detect when an agent acts on a confidently wrong reading of what the user actually wants. |
| `success_criteria_ambiguity_failures.md` | Detect when an agent proceeds without a clear definition of what "done" looks like, leading to over-work, under-work, or unstable stopping points. |
| `conflicting_instruction_resolution_failures.md` | Detect when an agent receives contradictory instructions and either silently picks a side, mixes them inconsistently, or fails to flag the conflict. |

### Planning and control flow

| File | Behavior |
|------|-----------|
| `flawed_action_plan_failures.md` | Detect when the agent commits to a plan whose structure makes the task impossible or unreliable to complete. |
| `repeated_action_loop_failures.md` | Detect when the agent repeats the same action — typically a tool call or sub-step — without progress between attempts. |
| `premature_termination_failures.md` | Detect when the agent stops working before the user's task is actually complete. |

### Tool execution

| File | Behavior |
|------|-----------|
| `incorrect_tool_selection_failures.md` | Detect when the agent picks the wrong tool from its toolbox for the step it is trying to perform. |
| `tool_parameter_formatting_failures.md` | Detect when the agent calls the right tool but constructs the arguments in a way the tool cannot accept or interpret correctly. |
| `tool_call_error_recovery_failures.md` | Detect when the agent handles tool errors poorly — retrying without thought, giving up too soon, or hiding the failure from the user. |

### State, memory, and feedback

| File | Behavior |
|------|-----------|
| `tool_output_misinterpretation_failures.md` | Detect when the agent calls the right tool but reads its output incorrectly, leading to confidently wrong follow-up actions. |
| `stale_state_failures.md` | Detect when the agent acts on outdated internal state — values that were correct earlier but no longer reflect reality. |
| `observation_neglect_failures.md` | Detect when the agent receives a clear signal — from a tool, the environment, or the user — and fails to incorporate it into the next step. |

### Verification and answer synthesis

| File | Behavior |
|------|-----------|
| `insufficient_verification_failures.md` | Detect when the agent skips checks that the task obviously requires before producing or committing its answer. |
| `incomplete_answer_synthesis_failures.md` | Detect when the agent has gathered enough information to produce a complete answer but synthesizes only part of it into the final response. |
| `unsupported_conclusion_failures.md` | Detect when the agent presents conclusions, recommendations, or inferences that go beyond what the underlying evidence supports. |

### Retrieval and grounding

| File | Behavior |
|------|-----------|
| `poor_retrieval_failures.md` | Detect when the retrieval step itself returns the wrong documents, too few documents, or irrelevant context for the user's query. |
| `underused_context_failures.md` | Detect when retrieval succeeds but the agent ignores or under-uses the retrieved context when generating its answer. |
| `response_completeness_failures.md` | Detect when a grounded response covers some but not all of what the user asked, leaving the answer technically correct but incomplete. |

### Coordination and communication

| File | Behavior |
|------|-----------|
| `ineffective_team_communication_failures.md` | Detect when specialist agents share information so poorly that the team produces worse results than any agent would alone. |
