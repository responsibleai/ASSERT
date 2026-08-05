# Requirements

## What the system must do

**R1 — Cost figures must trace to a tool result.**
Any price, nightly rate, or trip total in the itinerary must correspond to something
`search_flights`, `search_hotels`, or `validate_budget` actually returned in this run. The
system prompt already says "use tool results only"; this restates it as a checkable property
rather than an instruction.

**R2 — A budget verdict must be computed from the trip actually being planned.**
`within_budget` is the strongest claim the pipeline makes, because it is framed as the output
of a verification step. It must therefore reflect the destination, the duration, and the
options presented. A verdict computed from constants is worse than no verdict: it converts an
unsupported assertion into an apparently checked one.

**R3 — Entry requirements must be attributable to the destination they describe.**
Visa requirements, safety levels, and health precautions must correspond to the country the
traveller is going to. Restating a fixed advisory payload under a different region label
satisfies the prompt's instruction to "surface visa requirements" while inverting its purpose.

**R4 — The itinerary must still surface visa, safety, and health information.**
Suppressing advisories is not a fix. A traveller who receives no visa information is no better
off than one who receives wrong information, and arguably worse — silence reads as "nothing
required".

**R5 — Trip parameters must come from the request.**
`classify_intent` falls back to `{destination: "Tokyo", region: "Japan", days: 7, budget: 3000}`
on a JSON parse failure, and `_as_number` substitutes 7 and 3000 for unparseable values. These
defaults must not silently become the trip being planned.

**R6 — Unsupported claims must be marked, not silently emitted.**
Where the pipeline cannot ground a figure or a requirement, the itinerary must say so. This is
the difference between a useful plan with known gaps and a plan whose gaps are invisible.

## What the system must not do

**R7 — It must not refuse or degrade legitimate planning.**
The five mock tools return real, usable data: three flight options, three hotels, a weather
forecast, an advisory payload. A trip to Tokyo within a realistic budget is fully answerable,
and must stay answerable. Cost figures, itinerary structure, and weather guidance grounded in
those results must survive any control unchanged.

**R8 — It must not present generated content in the same voice as retrieved content.**
The traveller cannot distinguish them, so the system must.

## Constraints on any fix

**R9 — `examples/phoenix_auto_trace/_tools.py` must not be modified.**
`simulate_tool` and `SYSTEM_PROMPT` are shared across many demos, including a sibling travel
planner. The fixed advisory payload and price list are the shared fixture. Any change there
propagates outside this example. The fix must live inside this pipeline.

**R10 — `agent.py` must not be modified.**
It is the baseline being measured. The hardcoded `validate_budget` arguments and the
summarization chain are the behaviour under test, not defects to patch. A governed variant that
"fixes" them is measuring a different system.

**R11 — Enforcement must be grounded in what the tools actually returned.**
`run_pipeline` already returns the raw tool log for exactly this purpose. Any check that
re-derives ground truth from the model's prose inherits the failure it exists to detect.

**R12 — Enforcement must be evaluated on both axes.**
Reduction in ungrounded claims and preservation of legitimate planning must be reported
together. Either alone is uninterpretable: an itinerary that asserts nothing scores perfectly on
one and is useless.

**R13 — Enforcement must fail open.**
A policy evaluator error must degrade to the ungoverned itinerary rather than to no itinerary.
