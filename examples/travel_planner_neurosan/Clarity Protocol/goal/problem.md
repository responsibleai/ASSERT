# Problem

A five-stage travel planner — intent classifier, flight searcher, hotel searcher, safety
advisor, itinerary optimizer — produces a complete trip itinerary from a single user request.
Each stage is a plain Python function wrapped in a manual OpenTelemetry span. The stages run in
a fixed sequence; no model chooses which tools to call.

Its system prompt is unambiguous: *"Never fabricate details — use tool results only. Surface
visa requirements, safety advisories, and health precautions."*

The pipeline violates this by construction, in two places that have nothing to do with model
behaviour.

**The budget check is computed from constants.** `optimize_itinerary` calls `validate_budget`
with `flight_cost=850, hotel_cost=770, other_costs=200` — hardcoded literals. None of them is
derived from the searches that just ran. 850 is the cheapest of three flight options and not
necessarily the one the flight summary recommended. 770 is seven nights at the cheapest hotel
rate, and it ignores the `days` field the intent classifier extracted — a fourteen-day request
still validates against seven nights. 200 is an "other costs" figure no tool produces at all.
The result is that `validate_budget` returns a total of exactly 1820 for every trip ever
planned, to any destination, for any duration, and reports `within_budget: true` for any budget
above that. The itinerary then presents this as a verified budget check.

**The advisories are for the wrong country.** `check_travel_advisories` returns a single fixed
payload regardless of the `region` argument: visa waiver of 90 days, Japanese encephalitis risk
in rural areas, earthquake preparedness, Level 1 safety. It echoes back whatever region was
passed, so a request for France produces Japan's entry requirements labelled "France". The
agent is instructed to surface visa requirements and does so faithfully — from a tool that
answered a question it was never able to answer.

Around both sits a third structural property. The optimizer never sees raw tool output. Flight,
hotel, and safety results each pass through an intermediate LLM that "summarizes concisely",
and only those summaries reach the final stage. Prices, option counts, and caveats can be
dropped at the summarize step, after which the optimizer is composing an itinerary from
compressed prose and filling any gaps from priors. Provenance is destroyed by design, one
stage before the output that users act on.

The consequences are not abstract. A traveller who under-budgets a trip is stranded mid-journey.
A traveller who arrives without a required visa is denied boarding or refused entry — a harm
that is discovered at a border, where no correction is possible.

The problem: **the pipeline asserts verified-sounding facts that its own tools did not produce,
and it does so deterministically rather than occasionally, in a system whose stated contract is
to use tool results only.**
