# Architecture

## Where the control goes

`run_pipeline(message, history) -> (final_itinerary, raw_tool_results)` is the seam, and it was
built for this. The raw log accumulates in `_tool_call` through a `contextvars.ContextVar`, so it
is concurrency-safe and requires no monkeypatching, no module-global mutation, and no
duplication of the pipeline.

`agent_guarded.py` therefore:

1. calls `run_pipeline`, obtaining the itinerary and the exact tool results that produced it;
2. evaluates the itinerary against that log through ACS;
3. returns a string, with the same `chat(message, history)` signature.

Unchanged: the five stages, their order, `SYSTEM_PROMPT`, `_MODEL`, `temperature=0`,
`max_tokens=4000`, the intermediate summarization calls, the OTel spans. The only difference
between baseline and guarded is whether the output is evaluated against the log — which is what
licenses attributing any measured difference to enforcement.

Neither `agent.py` nor `examples/phoenix_auto_trace/_tools.py` is touched. The latter is shared
across many demos, so a fix there is not local to this example.

## The two checks

**Deterministic grounding check — cost and budget claims.**
The log contains everything needed:

- `search_flights` → prices 850, 1180, 1350
- `search_hotels` → nightly rates 110, 145, 195
- `validate_budget` → the args it was called with and the total it returned
- the intent's `days` and `budget`

Three conditions are decidable by comparison, with no judgement:

- `other_costs=200` appears in no tool result — an unsourced input to the verdict.
- `hotel_cost=770` is seven nights at the cheapest rate; wrong whenever `days != 7`.
- `total: 1820` is invariant across destination and duration.

Any monetary figure in the itinerary that does not reconcile to the log is ungrounded. Because
the inputs are constants, this check fires deterministically rather than probabilistically.

**Semantic annotator — entry requirements.**
Whether the visa, safety, and health statements are attributable to the destination is not
visible at any tool boundary: `check_travel_advisories` is called correctly and returns a
payload that happens to describe Japan. The check compares the itinerary's entry-requirement
claims against the destination being planned and the advisory payload actually returned.

This requires a host-dispatched annotator. **The annotator name must match in three places** —
the manifest key, `input.annotations.<name>` in the Rego, and the dispatcher branch in
`agent_guarded.py` — or the gate silently no-ops and reports success. It must fail open.

## Response design

Marking, not refusal, with one exception.

- **Cost claims** that fail grounding are regenerated against the real figures in the log. The
  flight and hotel results contain usable prices, so a grounded itinerary is achievable rather
  than merely safer.
- **The budget verdict** is the exception. Where the total cannot be computed from tool results
  and the actual duration, it must not be presented as verified at all. Its harm comes entirely
  from being framed as the output of a check; a hedged "verified" is still read as verified.
- **Entry requirements** that cannot be attributed to the destination are marked as unverified
  at the point they appear, with the traveller directed to an authoritative source. They are not
  removed: silence reads as "nothing required", which is the harm the requirement exists to
  prevent.

A flat refusal path must not be built. Precedent from a comparable domain: a blunt refusal
fallback drove scenario overrefusal to 84–92%, while a regenerate-and-re-gate design brought it
to 48% with harm falling 76% → 36%.

## Constraints

- **Fail open.** An evaluator error returns the ungoverned itinerary. No itinerary is worse than
  an imperfect one — the traveller simply goes elsewhere.
- **No state to track.** Every decision is a function of one turn's log and one turn's output.
- **Legitimate planning must survive.** Itineraries grounded in the real flight, hotel, and
  weather results must pass unchanged. This is the axis that a control tuned only for harm will
  destroy.

## Evaluation

Two numbers, reported together: ungrounded claims down, legitimate planning preserved. Neither is
interpretable alone. Because the budget fabrication is deterministic, a governed run that does
not move it is evidence of a gate that is not firing — not of a clean baseline.
