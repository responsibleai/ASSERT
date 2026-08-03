# ACS generator report: travel_itinerary_provenance_signal

## Assumptions

### Annotators
- `tool_grounding_classifier` (classifier) expected labels/outputs: none declared
- `provenance_signal_llm` (llm) expected labels/outputs: none declared
- `grounding_endpoint` (endpoint) expected labels/outputs: none declared

### JSONPaths
- `post_tool_call` policy_target `tool_result` at `$.tool_result`
  - tool name from `$.tool_call.name`
- `post_model_call` policy_target `model_response` at `$.model_response`
- `output` policy_target `assistant_output` at `$.output`

### Tools
- `budget_validation` from provided inventory
- `flight_search` from provided inventory
- `hotel_search` from provided inventory
- `travel_advisory` from provided inventory
- `weather_lookup` from provided inventory

## Not statically verified

- Classifier labels and scores match real annotator outputs.
- Policy intent fully captures the natural-language prompt.

## Warnings

- No tool inventory was provided beyond the named travel tools in the prompt; policy assumes these tool names for grounding classification.
- This plan enforces provenance signaling for mixed grounded/ungrounded itineraries, but does not itself redact or rewrite user-visible prose unless downstream mediation applies the transform annotations.
- Tools declared with minimal metadata (no inventory provided): budget_validation, flight_search, hotel_search, travel_advisory, weather_lookup
