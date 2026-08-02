# ACS generator report: assert_career_health_unsupported_inference_baseline

## Assumptions

### Annotators
- `invented_job_or_employer_classifier` (classifier) expected labels/outputs: none declared
- `unsupported_profile_completion_classifier` (classifier) expected labels/outputs: none declared
- `unsupported_evidence_span_classifier` (classifier) expected labels/outputs: none declared

### JSONPaths
- `output` policy_target `assistant_output` at `$.output`

### Tools
- No tools emitted; none were both requested and present in the provided inventory.

## Not statically verified

- Classifier labels and scores match real annotator outputs.
- Policy intent fully captures the natural-language prompt.

## Warnings

- These are semantic output guardrails and rely on annotators rather than deterministic structural checks.
- No tool inventory was provided, so no pre_tool_call or post_tool_call rules were authored.
- Annotators should be calibrated to avoid blocking faithful copying or cautious uncertainty statements grounded in the provided source.
