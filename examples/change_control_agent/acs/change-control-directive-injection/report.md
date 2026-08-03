# ACS generator report: change_management_embedded_directive_guardrails

## Assumptions

### Annotators
- `embedded_directive_detector` (classifier) expected labels/outputs: none declared
- `proposal_text_sanitizer` (llm) expected labels/outputs: none declared
- `policy_escalation_router` (endpoint) expected labels/outputs: none declared

### JSONPaths
- `input` policy_target `user_input` at `$.input`
- `pre_model_call` policy_target `model_request` at `$.model_request`
- `pre_tool_call` policy_target `tool_args` at `$.tool_call.args`
  - tool name from `$.tool_call.name`
- `post_model_call` policy_target `model_response` at `$.model_response`
- `output` policy_target `assistant_output` at `$.output`

### Tools
- No tools emitted; none were both requested and present in the provided inventory.

## Not statically verified

- Classifier labels and scores match real annotator outputs.
- Policy intent fully captures the natural-language prompt.

## Warnings

- Embedded directives must be treated as untrusted data and never as policy, approval, or authorization.
- Sanitization should preserve factual rollback and blast-radius content while stripping directive language before tool use.
