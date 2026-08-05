# Summary

## Problem

A five-stage travel pipeline — intent classifier, flight searcher, hotel searcher, safety
advisor, itinerary optimizer — produces complete trip itineraries. Its system prompt says
"never fabricate details — use tool results only." Two structural properties violate that
regardless of model behaviour.

`optimize_itinerary` calls `validate_budget` with hardcoded literals: `flight_cost=850,
hotel_cost=770, other_costs=200`. None derives from the searches that just ran, 770 assumes
seven nights whatever the extracted `days`, and 200 has no source in any tool. The verdict is
`total: 1820` for every trip, to every destination, for every duration — presented as a
verified budget check.

`check_travel_advisories` returns one fixed payload for any region: Japan's 90-day visa waiver,
Japanese encephalitis, earthquake preparedness. A request for France yields Japan's entry
requirements labelled "France". The agent surfaces them faithfully, as instructed.

Around both, the optimizer never sees raw tool output — flights, hotels, and safety each pass
through an intermediate "summarize concisely" LLM call, so provenance is destroyed one stage
before the text the traveller acts on.

## Stakeholders

The traveller, who cannot distinguish a retrieved figure from an invented one and discovers the
failure at an airline counter or a border. The traveller with a constrained passport, for whom
the fixed Japan payload is most confidently wrong exactly where the stakes are highest. Providers
and employers booking against an invariant "verified" total. The operator, who has no signal that
any of this is happening. The maintainers of the shared `_tools.py`, which cannot be modified
because other demos import it.

## Requirements

Cost figures must trace to a tool result. A budget verdict must be computed from the trip
actually being planned. Entry requirements must be attributable to the destination. Advisories
must still be surfaced — silence reads as "nothing required". Trip parameters must come from the
request, not from the `Tokyo/Japan/7/3000` fallback. Unsupported claims must be marked rather
than silently emitted. Legitimate planning must not degrade. Neither `agent.py` nor the shared
`_tools.py` may be changed. Enforcement must be grounded in tool results, fail open, and be
measured on harm and legitimate use together.

## Solution

Gate the output against the tool log the pipeline already returns. `run_pipeline` hands back
`(itinerary, raw_tool_results)` and says in its docstring that the log exists for exactly this;
it accumulates through a `contextvars.ContextVar`, so it is concurrency-safe and needs no
monkeypatching. The guarded agent calls `run_pipeline`, evaluates, and returns a string.

Cost claims are decidable arithmetic against the log — the real prices, rates, and `days` are all
there — and because the inputs are constants the check fires deterministically. Entry
requirements need a semantic annotator, since `check_travel_advisories` is called correctly and
merely returns the wrong country's data.

Response is marking and regeneration, not refusal. Ungrounded figures are regenerated against
real prices; unattributable advisories are marked unverified and the traveller is pointed at an
authoritative source. The budget verdict is the exception — where it cannot be computed from tool
results and the real duration, it must not be stated as verified at all.

## Failure Modes

Six modes, two Critical. **Fabricated budget verification** is the deterministic one: an
invariant 1820 total, framed as the output of a check. **Entry requirements for the wrong
country** is the one that harms travellers at borders, and it harms the constrained-passport
traveller most.

Below them: **ungrounded cost figures** asserted by the optimizer; **provenance collapse through
the summarization chain**, which is why the optimizer has nothing to ground against; **silent
default trip parameters** from the intent fallback; and **the enforcement layer's own failures** —
over-marking, a suppressed-advisory path that reintroduces the harm, and a gate that silently
no-ops on an annotator-name mismatch.

Success is two numbers reported together. Because the budget fabrication is deterministic, a
governed run that fails to move it is evidence of a gate that is not firing, not of a clean
baseline.
