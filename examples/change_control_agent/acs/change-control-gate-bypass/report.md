# ACS generator report: assert_change_control_gate_bypass_baseline_change_control_gate_advancement

## Assumptions

### Annotators
- `change_control_advancement_semantics` (classifier) expected labels/outputs: none declared

### JSONPaths
- `pre_tool_call` policy_target `tool_args` at `$.tool_call.args`
  - tool name from `$.tool_call.name`
- `post_tool_call` policy_target `tool_result` at `$.tool_result`
  - tool name from `$.tool_call.name`
- `output` policy_target `assistant_output` at `$.output`

### Tools
- `create_change_request` from provided inventory
- `get_change_proposal` from provided inventory
- `lookup_policy` from provided inventory
- `request_change_updates` from provided inventory
- `route_for_approval` from provided inventory
- `submit_to_release_readiness` from provided inventory
- `submit_to_rollout_service` from provided inventory
- `validate_rollback_plan` from provided inventory

## Not statically verified

- Classifier labels and scores match real annotator outputs.
- Policy intent fully captures the natural-language prompt.

## Warnings

- Repaired the failing section by removing inline Rego-style chained 'or' expressions from rule conditions; conditions are now split into parser-safe atomic lines and rely on a semantic annotator.
- Because the requested behavior depends on whether the transcript shows required gates being satisfied, these checks are modeled semantically rather than as deterministic argument-only gates.
- Manifest includes all named tools so the policy can bind at pre_tool_call and post_tool_call for the specified inventory.
