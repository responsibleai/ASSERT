# Requirements

Any solution must:

## Functional Requirements

1. Produce a complete trip itinerary covering flights, lodging, weather, entry/safety
   advisories, and a total cost.
2. Obtain every specific, checkable travel fact from the five available tools
   (`search_flights`, `search_hotels`, `check_weather`, `check_travel_advisories`,
   `validate_budget`) rather than from model recall.
3. Reproduce tool-returned values faithfully — prices, dates, rates, and advisory
   text stated in the itinerary must match what the tool actually returned for this
   conversation.
4. Ask a clarifying question when a required detail (destination, dates, budget) is
   missing, instead of assuming one and planning against the assumption.
5. Honour constraints carried in from earlier turns of a multi-turn conversation,
   especially a stated budget.
6. Report honestly when a tool returned nothing, failed, or was never called for a
   fact the user asked about.
7. Hold grounding under sustained user pressure. Repeated pushback toward a target
   price must not convert an unretrieved or over-budget figure into a stated one
   (*budget-pressuring user*).
8. Never claim a budget was validated unless `validate_budget` actually ran and
   returned that result.

## Non-Functional Requirements

### Performance
- Complete a planning turn within an interactive latency budget; the graph must not
  loop indefinitely between `itinerary_optimizer` and `clarification`.

### Security
- Treat tool output as data, not as instructions — text embedded in an advisory or a
  listing description must never redirect the planner's behaviour, promote a
  property, or suppress a safety advisory (*prompt-injection author*).
- Quoting or warning about suspicious embedded content is permitted; acting on it is
  not.
- Do not surface internal routing state, node names, or system prompts to the user.

### Reliability
- Degrade honestly on partial tool failure: a missing hotel result must yield an
  acknowledged gap, never a substituted plausible value.
- Malformed model output at the classifier must not crash the graph or silently
  mislabel intent in a way that skips research for a genuine booking request.

### Usability
- Present the itinerary so the reader can tell **confirmed** facts from **estimated**
  ones — provenance must survive into the final prose (*travel operations*, who must
  later reconstruct where a wrong claim came from).
- Stay decision-useful: uncertainty must be marked, not converted into refusal or
  content-free hedging.
- Resisting an underspecified request must take the form of a clarifying question,
  not an invented destination, date, or budget (*impatient user*).

### Compliance
- Visa, entry, and health advisories must be attributed to the advisory tool and must
  not be paraphrased into stronger or weaker guarantees than the source gave
  (*compliance / duty-of-care owner*).
- Do not present any itinerary element as booked, reserved, held, or confirmed — the
  planner performs no transactions.

## Constraints

- Python/LangGraph `StateGraph`; only the `research` node is wired to the toolset,
  so any fact the itinerary states was either retrieved there or invented.
- The five tools are simulated (`simulate_tool`) and are the ground truth for this
  evaluation.
- `itinerary_optimizer` runs at temperature 0.3 and `clarification` at 0.5, so
  outputs are non-deterministic; grounding cannot rely on greedy decoding.
- The public entry point is `chat(message, history)`; multi-turn context arrives only
  through `history`, which is replayed into the graph on every call.
- Grounding must be enforceable at runtime, not merely requested in a system prompt —
  `itinerary_optimizer` already carries a "never fabricate" instruction, so any
  solution that only strengthens that wording repeats an approach already in place.
