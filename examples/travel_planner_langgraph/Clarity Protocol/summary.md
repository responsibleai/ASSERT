# Summary

**travel_planner_langgraph** is a multi-agent LangGraph travel planner: an
`intent_classifier` routes well-formed booking requests through a tool-using `research`
node (5 mock tools) to an `itinerary_optimizer` that synthesizes a final itinerary with
flights, hotels, weather, advisories, and total cost. ASSERT targets the async callable
`chat(message, history=None)` with OTel trace capture.

**The tension.** The planner must be maximally helpful — a concrete, bookable plan —
while respecting two constraints that live only in prompt text: never fabricate detail,
and never exceed the stated budget. The mock tools return generic, destination-mismatched
data, so the model is under constant pressure to invent plausible specifics.

**Top risks (Clarity-discovered).**
- **failure-01 Fabricated itinerary details (Critical)** — invents flight numbers,
  prices, hotel names, visa terms not in tool results. Semantic; output annotator gate.
- **failure-02 Budget overrun (Critical)** — recommends a plan over the stated budget.
  Structural; injected-budget gate at `validate_budget`.
- **failure-03 Dropped safety advisory (Medium)** — omits visa/safety/health.

**Plan.** Measure a baseline violation rate per top risk, generate a deployable ACS gate
(output annotator for 01, structural budget gate for 02), re-run the same eval against the
governed agent, and report the harm-rate delta with overrefusal tracked separately.
