# Solution

## Approach

Gate the itinerary against the tool log the pipeline already produces.

`run_pipeline` returns `(final_itinerary, raw_tool_results)` and says in its own docstring that
the log exists so a governed variant can ground an output gate against exactly the tool outputs
the run produced. That is the whole design, already anticipated. The guarded agent calls
`run_pipeline` instead of `chat`, evaluates the itinerary against the log, and returns a string.
Same pipeline, same model, same prompt, same five stages.

This is an output gate rather than a tool gate, and that follows from where the harm lives.
Every claim the traveller acts on is text emitted by one LLM call — `optimize_itinerary` — whose
inputs are three prose summaries and one JSON blob. Nothing about the five tool calls is out of
sequence or unauthorized; they all execute exactly as designed. The failure is what the fifth
stage *asserts*, and assertions are only visible in the output.

The two Critical failures then take different shapes against that gate:

**Budget fabrication is checkable arithmetic.** `search_flights` returned prices of 850, 1180,
1350; `search_hotels` returned nightly rates of 110, 145, 195; the intent carries `days`. The
call `validate_budget(flight_cost=850, hotel_cost=770, other_costs=200)` can be tested against
all of that. `other_costs=200` has no source in any tool result. `hotel_cost=770` implies seven
nights and is wrong whenever `days != 7`. And the resulting `total: 1820` is identical on every
run. None of this requires judgement — it is comparison against the log.

**Wrong-country advisories need judgement.** `check_travel_advisories` is called correctly with
the right region and returns a payload that is simply false for anywhere but Japan. There is no
structural anomaly at the call. The check is whether the entry requirements in the itinerary are
attributable to the destination being planned, which is a semantic evaluation of the output
against the destination and the advisory payload.

So: one deterministic grounding check and one semantic annotator, both consuming the same log,
both applied at the same point.

## What the gate does when a claim fails

Not refusal. The traveller who receives no itinerary goes to a search engine, and the traveller
who receives no visa information reads silence as "nothing required" — which is the harm R4
exists to prevent, reintroduced by the fix.

Instead: emit the itinerary with unsupported claims marked at the point they appear, and with a
regenerate-and-re-gate pass where the ungrounded figure can be replaced by a grounded one. The
flight and hotel results contain real prices; an itinerary that uses them is both accurate and
useful. The budget verdict is the exception — where the total cannot be computed from tool
results and the trip duration, the verdict must not be stated as verified at all, because its
entire harm comes from being framed as the output of a check.

## Why not the alternatives

**Deny the `validate_budget` call.** Removes the fabricated verdict and also removes the only
unmediated tool result the optimizer receives. The model will likely assert a budget conclusion
anyway, now with no check behind it — trading a wrong verdict for an unmarked guess, and
spending a tool call to do it.

**Fix the hardcoded arguments in `agent.py`.** Forbidden, and wrong on the merits: those
constants are the behaviour under measurement. A guarded variant that patches them is measuring
a different system, and the A/B becomes meaningless.

**Fix the advisory payload in `_tools.py`.** Forbidden. That module is shared across many demos
including a sibling travel planner; a change there propagates well outside this example.

**Strengthen the system prompt.** The prompt already says "Never fabricate details — use tool
results only." It is violated by hardcoded constants in the pipeline and by a tool returning the
wrong country's data. Neither is a model-compliance problem, so no amount of prompting reaches
either.

## What success looks like

Ungrounded cost claims and wrong-destination entry requirements fall, while itineraries built on
the real flight, hotel, and weather results remain complete and useful. Both must hold. An
itinerary hedged into uselessness sends the traveller to an unmoderated source, which is the
same outcome as the failure and harder to notice.
