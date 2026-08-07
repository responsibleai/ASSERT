# Observations

Notes on this pipeline that do not belong to any single failure mode.

## Three lines of code contain both Critical failures

```python
budget_check = _tool_call("validate_budget", {
    "flight_cost": 850, "hotel_cost": 770, "other_costs": 200, "budget": budget,
})
```

Every value except `budget` is a literal. 850 is the cheapest flight option, 770 is seven nights
at the cheapest hotel rate, 200 is nothing at all. The `days` variable is in scope and unused. The
flight and hotel results are in the tool log and unused.

And in the shared tool module, `check_travel_advisories` ignores its `region` argument entirely
and returns a fixed Japanese payload with the requested region label pasted on.

Neither is a model failure. Both would occur with a perfectly compliant model, and both persist
under any prompt. This is the strongest available evidence that the problem is architectural: the
system prompt says "never fabricate details — use tool results only", and the pipeline fabricates
details in Python before the model is ever consulted.

## The failures are deterministic, and that is worth exploiting

`validate_budget` receives identical inputs on every run, so it returns `total: 1820` every time.
This is unusual in this class of work and it has three consequences worth planning around.

The baseline measures a property of the pipeline rather than a tendency of the model, so it should
be stable across runs and across sampling temperature. Any variance in the measured rate reflects
how the optimizer *reports* the verdict, not whether the verdict is fabricated.

It gives an exceptionally reliable sentinel for under-enforcement. A gate that silently no-ops —
because an annotator name is misspelled in one of three places, say — will leave that number
untouched. In a probabilistic domain a flat result is ambiguous; here it is close to proof that
the gate is not firing.

And it means the harm is fully reproducible by hand. Any doubt about whether the gate works can be
settled by running one turn and reading the trajectory, rather than by inferring from an aggregate.

## Fidelity to tools is the problem, not the solution

The usual framing of grounding failures is that the model departs from its evidence, so the fix is
to bind it more tightly to tool output.

Failure 02 inverts that. `check_travel_advisories` returns Japan's entry requirements for France,
and the agent reports them accurately. The system prompt instructs it to surface visa requirements;
it complies. A more faithful model produces the identical harm, and a check on whether the agent
stayed consistent with its tool results would pass this case cleanly.

The check therefore has to be about *attribution* — do these entry requirements belong to this
destination — rather than about consistency. That is a semantic judgement, and it is why one of the
two Critical failures cannot be handled by the same mechanism as the other.

## The tools return good data; nothing consumes it

Three real flight options with prices, three hotels with nightly rates and ratings, a weather
forecast with an actionable recommendation. Every failure chain in this system begins with correct
retrieved data and proceeds by discarding it.

The practical consequence for the fix is that **regeneration should be preferred to hedging almost
everywhere.** When a cost claim fails grounding, the real prices are sitting in the log — the gate
can produce a correct itinerary rather than a cautious one. That is a materially better position
than domains where the harmful claim has no true counterpart, and it is why over-marking would be
a self-inflicted failure rather than a necessary trade.

The advisory payload is the exception: there is no correct data available for a non-Japan
destination, so marking plus redirection is the best achievable outcome.

## Two responses, because belief forms in two different places

Marking works for entry requirements and does not work for the budget verdict, and the reason is
worth stating explicitly.

A visa requirement flagged "unverified — confirm with the embassy" loses its authority. The
traveller now knows they have to check, which is the correct end state.

A budget verdict flagged the same way still appears under a headline of "$1,820 total, within
budget". The traveller reads the number. The caveat is a footnote on a figure that has already
done its work — and the figure's entire harm is that it is framed as the output of a check.
Hedging a verification does not un-verify it.

So the budget verdict must not be stated as verified at all where it cannot be computed from tool
results and the real duration, while advisories should be marked rather than removed. Applying one
policy uniformly fails one of the two.

## `region` defaulting quietly defeats the obvious gate

The natural implementation for failure 02 compares the advisory payload's region against the
requested region and flags a mismatch.

`classify_intent` defaults `region` to "Japan" on any JSON parse failure. When that fires, the
requested region *is* Japan and the payload *is* Japan's, so the comparison agrees — while the
traveller, who asked about Portugal, receives Japanese entry requirements. The gate reports
consistency on a falsehood.

Attribution must therefore be evaluated against the destination the traveller actually asked for,
not against the region field the pipeline derived. Parameter provenance is part of what needs
verifying, not trusted context to verify against.

## Boundaries on any change

`agent.py` is the baseline under measurement. Its hardcoded arguments and its summarization chain
are the behaviour being tested, not defects to repair — a guarded variant that fixes them measures
a different system and the A/B becomes meaningless.

`examples/phoenix_auto_trace/_tools.py` is shared across many demos, including a sibling travel
planner. A change to the advisory payload or the price list there propagates well outside this
example and would silently alter another domain's baseline.

The `run_pipeline` seam exists precisely so that neither needs to move. It returns the itinerary
and the raw tool log, is `contextvars`-based and therefore concurrency-safe, and requires no
monkeypatching. A guarded agent that calls it, evaluates, and returns a string differs from the
baseline in exactly one respect — which is what licenses attributing any measured difference to
enforcement.
