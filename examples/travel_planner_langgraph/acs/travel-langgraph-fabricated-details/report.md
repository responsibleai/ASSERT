# ACS generator report: travel_langgraph_fabricated_details_baseline

## Assumptions

### Annotators
- `travel_fabricated_details_semantic` (llm) expected labels/outputs: none declared

### JSONPaths
- `pre_tool_call` policy_target `tool_args` at `$.tool_call.args`
  - tool name from `$.tool_call.name`
- `post_tool_call` policy_target `tool_result` at `$.tool_result`
  - tool name from `$.tool_call.name`
- `output` policy_target `assistant_output` at `$.output`

### Tools
- `check_travel_advisories` from provided inventory
- `check_weather` from provided inventory
- `search_flights` from provided inventory
- `search_hotels` from provided inventory

## Not statically verified

- Classifier labels and scores match real annotator outputs.
- Policy intent fully captures the natural-language prompt.

## Warnings

- Repaired prior validation failure by removing inline boolean-or style conditions and splitting tool gating into separate rules with simple equality checks.
- These are semantic annotator-based guardrails because the requested violation classes depend on whether content is supported by current tool outputs; that support relation is not deterministically decidable from only input.policy_target.value and input.tool.name/id in this schema.
- Offline validate will not execute the LLM annotator, so semantic enforcement is expected to be runtime-only.
