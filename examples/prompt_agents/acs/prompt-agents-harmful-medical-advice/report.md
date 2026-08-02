# ACS generator report: assert_prompt_agents_harmful_medical_advice_baseline_chat

## Assumptions

### Annotators
- `medical_overconfident_reassurance_classifier` (classifier) expected labels/outputs: none declared
- `unsafe_individualized_dosing_classifier` (classifier) expected labels/outputs: none declared
- `unsafe_medication_combination_classifier` (classifier) expected labels/outputs: none declared
- `high_risk_factor_omission_classifier` (classifier) expected labels/outputs: none declared
- `unsupported_diagnosis_treatment_classifier` (classifier) expected labels/outputs: none declared

### JSONPaths
- `output` policy_target `assistant_output` at `$.output`

### Tools
- No tools emitted; none were both requested and present in the provided inventory.

## Not statically verified

- Classifier labels and scores match real annotator outputs.
- Policy intent fully captures the natural-language prompt.

## Warnings

- These are semantic output guardrails and rely on annotators rather than deterministic string matching, to generalize across paraphrases and novel unsafe medical advice.
- No tool inventory was provided, so no pre_tool_call or post_tool_call structural rules were added.
- Offline validation that does not execute annotators may not trigger these rules; that is expected for semantic policies.
