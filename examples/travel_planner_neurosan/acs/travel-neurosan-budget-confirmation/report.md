# ACS generator report: travel_budget_verification_guardrail

## Assumptions

### Annotators
- `budget_verdict_detector` (classifier) expected labels/outputs: none declared
- `budget_verification_claim_detector` (llm) expected labels/outputs: none declared

### JSONPaths
- `post_model_call` policy_target `model_response` at `$.model_response`
- `output` policy_target `assistant_output` at `$.output`

### Tools
- No tools emitted; none were both requested and present in the provided inventory.

## Not statically verified

- Classifier labels and scores match real annotator outputs.
- Policy intent fully captures the natural-language prompt.

## Warnings

- Budget claims must not be affirmed unless usable retrieved prices support the verdict.
- If prices cannot be derived from current-turn retrievals, the response must explicitly leave the budget question open.
- General affordability guidance is allowed only when clearly marked as non-validated guidance.
