# Open Questions

## Answered by reading the system

**Is there a seam for a governed variant?**
Yes, and it was built deliberately. `run_pipeline(message, history)` returns
`(final_itinerary, raw_tool_results)`, and its docstring states the log exists so "a governed
variant can ground its output gate against the exact tool outputs this run produced. The
baseline `chat` discards the log." The log is accumulated through a `contextvars.ContextVar`,
so it is concurrency-safe and requires no monkeypatching. A guarded agent calls `run_pipeline`,
evaluates the itinerary against the log, and returns a string — same signature, same pipeline,
same model.

**Where is the harm produced?**
In `optimize_itinerary`, the fifth stage. Every claim the traveller acts on is text emitted by
one LLM call whose inputs are three prose summaries and one budget-check JSON. That is a single,
well-defined output to gate.

**Can a tool-call gate reach the failures?**
Partly, and the two Critical failures differ here. The budget fabrication has a structural
signature: `validate_budget` is invoked with `flight_cost=850, hotel_cost=770, other_costs=200`,
and those values can be checked against the flight and hotel results already in the log before
the call executes. The advisory failure has none — `check_travel_advisories` is called correctly
with the right region and returns a payload that is simply wrong; nothing at the call boundary
is anomalous.

**Is the failure probabilistic or deterministic?**
The budget path is deterministic. `validate_budget` receives the same three constants on every
run and therefore returns `total: 1820` always. This is unusual and valuable: the Critical
failure does not depend on model sampling, so a baseline measurement is measuring a property of
the pipeline rather than a tendency of the model.

**Does the optimizer see raw tool output?**
No. Flights, hotels, and safety each pass through an intermediate "summarize concisely" LLM
call, and only the summaries reach `optimize_itinerary`. Only `validate_budget`'s JSON arrives
unmediated. Provenance is destroyed one stage before the output.

**What can be changed?**
Not `agent.py` — it is the baseline. Not `examples/phoenix_auto_trace/_tools.py` — it is shared
with other demos, including a sibling travel planner. The fix must live in a new guarded module.

## Genuinely open

**Should a wrong-country advisory be corrected or withheld?**
R3 says advisories must match the destination; R4 says advisories must still be surfaced.
When the only available advisory payload is Japan's and the trip is to France, both cannot be
satisfied. Withholding risks a traveller reading silence as "no visa required" — the harm R4
exists to prevent. Marking the claim as unverified preserves the information while removing its
authority, but only if the marking survives into what the traveller actually reads.

**Is `region` even reliable?**
It is extracted by the intent LLM and defaults to "Japan" whenever intent parsing fails. So a
mismatch between destination and advisory can arise either from the fixed payload or from a
misextracted region, and a gate keyed on region comparison needs to know which.

**How much does the summarization chain drop?**
If the flight summary omits prices entirely, the optimizer has no grounded figure to use and
whatever it states is invented. If so, cost fabrication is not an occasional lapse but the
expected behaviour, and the volume of ungrounded claims in the baseline will be high.
Establishing this changes how the baseline number should be read.

**Does a pre-call gate on `validate_budget` help or hurt?**
Denying the call removes a fabricated verdict, but it also removes the only unmediated tool
result the optimizer receives, and the model may then assert a budget conclusion with no check
at all — trading a wrong verdict for an unmarked guess. Annotating the result may be safer than
blocking the call, but that needs testing rather than assuming.

**Where does the traveller's belief actually form?**
If the itinerary carries a caveat but the headline still reads "$1,820 total, within budget",
the caveat is decorative. Whether marking is sufficient, or whether the unsupported figure must
not appear at all, is the central unresolved design question.
