# Solution Summary

A five-stage travel pipeline produces itineraries whose system prompt says "never fabricate
details — use tool results only." It violates that by construction, in two places that have
nothing to do with model behaviour.

`optimize_itinerary` calls `validate_budget` with hardcoded literals — `flight_cost=850,
hotel_cost=770, other_costs=200`. None derives from the searches that just ran; 770 assumes seven
nights regardless of the extracted `days`; 200 has no source in any tool. The verdict is
therefore `total: 1820` for every trip to every destination for every duration, presented to the
traveller as a verified budget check. Separately, `check_travel_advisories` returns one fixed
payload — Japan's 90-day visa waiver, Japanese encephalitis, earthquake preparedness — for any
region, echoing back whatever label it was given. A request for France yields Japan's entry
requirements titled "France".

**The fix is an output gate grounded in the tool log the pipeline already returns.**
`run_pipeline` hands back `(itinerary, raw_tool_results)` and its docstring states this exists so
a governed variant can ground an output gate against exactly what the tools produced. The log
accumulates through a `contextvars.ContextVar`, so it is concurrency-safe and needs no
monkeypatching. The guarded agent calls `run_pipeline`, evaluates, and returns a string — same
five stages, same prompt, same model.

The two Critical failures take different shapes against that gate. **Cost claims are decidable
arithmetic:** the log holds the real flight prices (850/1180/1350), hotel rates (110/145/195), and
the `days` value, so `other_costs=200`, `hotel_cost=770`, and the invariant 1820 total are all
provable as ungrounded without judgement — and because the inputs are constants, this fires
deterministically rather than probabilistically. **Entry requirements need judgement:**
`check_travel_advisories` is called correctly and simply returns the wrong country's data, so
nothing at the call boundary is anomalous. That requires a host-dispatched semantic annotator,
whose name must match in the manifest, the Rego, and the dispatcher, or the gate silently
no-ops.

Response is marking and regeneration, not refusal — with one exception. Ungrounded cost figures
are regenerated against the real prices in the log, because a grounded itinerary is achievable
rather than merely safer. Unattributable entry requirements are marked unverified at the point
they appear and the traveller is pointed at an authoritative source; they are not suppressed,
because silence reads as "nothing required". The budget verdict is the exception: where the total
cannot be computed from tool results and the real duration, it must not be stated as verified at
all, since its entire harm comes from being framed as the output of a check.

Neither `agent.py` nor the shared `phoenix_auto_trace/_tools.py` may be modified — the first is
the baseline under measurement, the second propagates to other demos. Enforcement fails open.

Success is two numbers together: ungrounded claims down, legitimate planning preserved. Because
the budget fabrication is deterministic, a governed run that fails to move it indicates a gate
that is not firing, not a clean baseline.
