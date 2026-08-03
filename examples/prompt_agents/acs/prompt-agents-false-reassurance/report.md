# ACS generator report: false_reassurance_guardrail

## Assumptions

### Annotators
- `risk_classifier` (classifier) expected labels/outputs: none declared
- `safety_llm` (llm) expected labels/outputs: none declared
- `tool_facts` (endpoint) expected labels/outputs: none declared

### JSONPaths
- `post_model_call` policy_target `model_response` at `$.model_response`
- `output` policy_target `assistant_output` at `$.output`

### Tools
- No tools emitted; none were both requested and present in the provided inventory.

## Not statically verified

- Classifier labels and scores match real annotator outputs.
- Policy intent fully captures the natural-language prompt.

## Warnings

- Stateful enforcement should rely on recorded tool findings and prior annotations; do not treat general-population norms as sufficient for reassurance.
