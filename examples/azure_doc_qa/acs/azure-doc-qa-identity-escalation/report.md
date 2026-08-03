# ACS generator report: azure_docs_assistant_clearance_gate

## Assumptions

### Annotators
- `validated_principal_present` (classifier) expected labels/outputs: none declared
- `caller_self_description_claims_clearance` (classifier) expected labels/outputs: none declared
- `internal_retrieval_requested` (classifier) expected labels/outputs: none declared

### JSONPaths
- `pre_tool_call` policy_target `tool_args` at `$.tool_call.args`
  - tool name from `$.tool_call.name`
- `post_tool_call` policy_target `tool_result` at `$.tool_result`
  - tool name from `$.tool_call.name`
- `output` policy_target `assistant_output` at `$.output`

### Tools
- `caller_verification` from provided inventory
- `internal_retrieval` from provided inventory

## Not statically verified

- Classifier labels and scores match real annotator outputs.
- Policy intent fully captures the natural-language prompt.

## Warnings

- Do not treat caller self-description as authorization.
- Do not claim identity or clearance has been verified unless a validated principal is present.
- Internal retrieval must be blocked before execution for external callers.
- Escalation must accompany, not replace, a substantive public-documentation answer when available.
- Tools declared with minimal metadata (no inventory provided): caller_verification, internal_retrieval
