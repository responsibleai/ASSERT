# ACS generator report: acs_retrieval_inference_guardrail

## Assumptions

### Annotators
- `sharing_class_classifier` (classifier) expected labels/outputs: none declared
- `restricted_inference_llm` (llm) expected labels/outputs: none declared

### JSONPaths
- `input` policy_target `user_input` at `$.input`
- `pre_model_call` policy_target `model_request` at `$.model_request`
- `post_model_call` policy_target `model_response` at `$.model_response`
- `output` policy_target `assistant_output` at `$.output`

### Tools
- No tools emitted; none were both requested and present in the provided inventory.

## Not statically verified

- Classifier labels and scores match real annotator outputs.
- Policy intent fully captures the natural-language prompt.

## Warnings

- Preserve public and external_safe summarization with attribution.
- Do not suppress ordinary public research assistance.
- Restricted documents must be prevented from reaching the model whenever possible.
