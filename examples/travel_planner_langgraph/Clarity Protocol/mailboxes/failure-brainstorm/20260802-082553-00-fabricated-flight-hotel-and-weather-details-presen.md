# Fabricated flight, hotel, and weather details presented as retrieved facts

**Source:** mcp
**Pre-existing:** Yes

The travel planner states concrete, checkable trip facts -- flight numbers, departure and arrival times, fares, hotel names and nightly rates, and weather forecasts -- that were never returned by any tool call. The user cannot tell these apart from the grounded ones, because the agent presents fabricated and retrieved details in the same confident itinerary prose with no distinction between them.

This is the highest-severity failure in the domain because travel details are acted upon. A fabricated flight number is not an abstract inaccuracy; it is a traveler at the wrong terminal, a booking that cannot be found, or a missed connection. The harm is realized outside the conversation, after the user has stopped reading, which means no in-conversation caveat reliably reaches them.

The failure is a coverage gap, not a hallucination impulse. The agent is asked for a complete itinerary, has partial data, and treats the gap between "what I was asked to cover" and "what I actually retrieved" as something to be filled rather than reported.

## Additional Context

Measured, not hypothetical. The ungoverned baseline eval (25 prompt rows + 25 scenario rows, built-in judge dimensions) scored policy_violation at 20.0% on prompts and 60.0% on scenarios -- the worst scenario slice of any domain in this batch. Overrefusal was 0.0% / 24.0%.

Two structural properties of the agent explain why the scenario number is three times the prompt number:

1. `research` performs exactly ONE tool round. Whatever the tools return on that single pass is all the grounding the agent will ever have; there is no second attempt to fill what is missing.

2. `route_after_intent` sends any request that is not classified `book_trip` to a `clarification` node that has NO TOOLS AT ALL. In that branch the model is asked to be helpful about flights, weather, and hotels while holding literally zero retrieved data.

The mock tool layer is fully deterministic, so this is not model nondeterminism leaking through -- the same missing-data condition reproduces every run. The agent is asked to fill sections it was never given data for, and it complies by inventing them.

Multi-turn scenarios amplify this because each additional turn expands the itinerary surface the agent has committed to, while the grounding set stays frozen at whatever the single research round returned.
