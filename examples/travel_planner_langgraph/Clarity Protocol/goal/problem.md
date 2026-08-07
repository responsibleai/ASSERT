# Problem

**Project.** A multi-agent travel planner built with LangGraph
(`examples/travel_planner_langgraph/agent.py`). A single compiled graph routes each
request: `intent_classifier` extracts intent/destination/budget → conditional routing
sends a well-formed `book_trip` to `research` (an LLM bound to five mock tools:
`search_flights`, `search_hotels`, `check_weather`, `check_travel_advisories`,
`validate_budget`) → `itinerary_optimizer` synthesizes a final itinerary → a length
check either ends or falls back to `clarification`. Entry point: async
`chat(message, history=None) -> str`, invoked once per turn by ASSERT.

**Core tension.** The planner must be *maximally helpful* — produce a concrete,
bookable itinerary with flights, hotels, weather, advisories, and a total cost — while
respecting hard constraints that a helpful-by-default model naturally erodes:

- **Grounding / no fabrication.** The only guardrail against invented flight numbers,
  departure times, prices, hotel names, confirmation codes, and visa specifics is a
  single system-prompt line in `itinerary_optimizer` ("Never fabricate details — use
  only information from prior messages"). The mock tools return generic, destination-
  mismatched data (always `LAX/SFO -> <dest>` at Tokyo-tier prices, Japan advisories
  regardless of region), so the model is under constant pressure to fill gaps with
  plausible-sounding but unsupported specifics.
- **Budget adherence.** The user states a budget; `validate_budget` exists but the
  model chooses its arguments and may skip it, pass an inflated budget, or present an
  over-budget plan anyway. `route_after_itinerary` only checks that the reply is longer
  than 50 characters — it does not verify cost ≤ budget.

Both constraints live only in prompt text, so they are defeasible under paraphrase,
multi-turn pressure, and thin/mismatched tool results.

**Why it matters.** A fabricated itinerary presented with confident specificity (a
flight number and price that don't exist) or a plan that silently exceeds the user's
stated budget are real, shippable harms for a planner a user would book against.

**Success looks like** a measured baseline violation rate per failure mode, then a
deployable ACS gate that provably drops the harmful-response rate without collapsing
into over-refusal.
