# ACS generator report: travel_planning_grounding_guardrails

## Assumptions

### Annotators
- `destination_mismatch` (classifier) expected labels/outputs: none declared
- `grounding_check` (llm) expected labels/outputs: none declared
- `budget_validation_check` (classifier) expected labels/outputs: none declared

### JSONPaths
- `post_tool_call` policy_target `tool_result` at `$.tool_result`
  - tool name from `$.tool_call.name`
- `post_model_call` policy_target `model_response` at `$.model_response`
- `output` policy_target `assistant_output` at `$.output`

### Tools
- `check_travel_advisories` from provided inventory
- `check_weather` from provided inventory
- `search_flights` from provided inventory
- `search_hotels` from provided inventory
- `validate_budget` from provided inventory

## Not statically verified

- Classifier labels and scores match real annotator outputs.
- Policy intent fully captures the natural-language prompt.

## Warnings

- Do not treat search results as bookings, reservations, confirmations, holds, guarantees, or live availability.
- Do not present concrete travel facts unless explicitly returned by tools for the requested destination.
- If retrieved records mismatch the requested destination or region, surface the mismatch rather than relabeling the data.
- Budget compliance claims require validate_budget over the actual recommended flight and hotel options.
