# Failure: Fabricated budget verification

## Summary

`optimize_itinerary` calls `validate_budget` with three hardcoded literals:

```python
budget_check = _tool_call("validate_budget", {
    "flight_cost": 850, "hotel_cost": 770, "other_costs": 200, "budget": budget,
})
```

None of them derives from the searches that just ran. 850 is the cheapest of three flight
options, not necessarily the one the flight summary recommended. 770 is seven nights at the
cheapest hotel rate, and the `days` value the intent classifier extracted is never consulted —
a fourteen-day request validates against seven nights. 200 is an "other costs" figure no tool
produces at all.

So `validate_budget` returns `total: 1820` for every trip ever planned, to any destination, for
any duration, and reports `within_budget: true` for any budget above that. The itinerary then
presents this to the traveller as a verified budget check.

Two things make this the most serious failure in the pipeline. It is **deterministic** — not a
model tendency that appears under pressure, but a property of the code that fires on every
single run. And the harm comes specifically from the framing: an unsupported price is a claim
the traveller might question, whereas a number returned by a function called `validate_budget`
is *verification*. The system manufactures exactly the confidence that should have been earned
by checking.

## Failure Chain

1. A traveller asks for a trip within a stated budget.
   - *Observation:* This is the pipeline's core use case, so the failure is not reached by an
     edge case — it is the normal path.
2. `classify_intent` extracts `destination`, `region`, `days`, and `budget`. All four are
   available to the rest of the pipeline.
3. `search_flights` and `search_hotels` run and return real option sets — prices 850/1180/1350
   and nightly rates 110/145/195 — which are summarized to prose and passed forward.
   - *Observation:* The grounding data exists and is correct at this point. The failure is not a
     retrieval gap; it is that the retrieved values are then ignored.
4. `optimize_itinerary` calls `validate_budget` with the three constants.
   - *Observation:* `days` is in scope and unused; the flight and hotel results are in the tool
     log and unused. Nothing was unavailable.
   - *Intervention point (prevention):* Check the call's arguments against the flight and hotel
     results already in the log. `other_costs=200` matches no tool result; `hotel_cost=770`
     implies seven nights; the resulting total never varies. All three are decidable by
     comparison, without judgement.
5. The tool faithfully computes `total: 1820` and a `within_budget` verdict against the
   traveller's real budget.
   - *Observation:* The tool is not broken. It answers exactly the question it was asked. The
     defect is entirely in the inputs, which is why the trace looks clean — a span records that
     `validate_budget` ran and what it returned.
6. The optimizer composes an itinerary incorporating the verdict, in the same voice as everything
   else.
   - *Intervention point (detection):* Reconcile every monetary figure in the itinerary against
     the tool log; flag any that does not trace to a result.
7. The traveller reads a verified total and books. **harm begins**
   - *Observation:* The traveller cannot evaluate the figure — that is why they asked. And
     "within budget" is not a claim they would think to check, because it is presented as the
     outcome of a check.
   - *Intervention point (mitigation):* Do not state a budget verdict as verified where the
     total cannot be computed from tool results and the real duration. Marking is insufficient
     here specifically: a hedged "verified" is still read as verified.
8. **Branch point — long trip.** A fourteen-day request was validated against seven nights of
   accommodation. The traveller is short by roughly half the lodging cost.
9. **Branch point — expensive destination.** The trip was validated against Tokyo's mock prices
   regardless of where they are going.
10. The shortfall is discovered mid-trip, in a foreign country, where correction means emergency
    borrowing or cutting the trip short. **harm ends** when they get home.
    - *Intervention point (recovery):* Retain the tool log alongside the itinerary so an
      unsupported verdict can be identified after the fact.
11. Because the total is invariant, the error looks like a standard rather than a defect.
    Providers and employers reimbursing against it see a stable number and treat it as a policy
    baseline.

## Observations

- **Severity:** Critical — Direct financial harm to the traveller, discovered where it cannot be
  corrected, reached on the normal path with no adversary and no unusual phrasing. Deterministic
  rather than probabilistic: it occurs on every run. Rated at the top alongside the advisory
  failure because the verification framing removes the traveller's last reason to doubt, and
  because the invariance disguises the defect as a convention.
- **Related failures:** Distinct from *Ungrounded cost figures in the itinerary*, which is the
  model inventing prices; here the pipeline supplies the invented inputs itself and the model is
  faithful. *Provenance collapse through the summarization chain* is why no downstream stage can
  catch it. *Silent default trip parameters* can corrupt the `budget` argument as well, making
  even the comparison meaningless. The enforcement-layer mode covers the risk of marking the
  verdict without removing its authority.
- **Variants:**
  - Budget verdict computed from constants *(brainstorm)* — the invariant 1820 total
  - Trip duration ignored in budget total *(brainstorm)* — `days` extracted and unused
  - Unsourced other costs enter the total *(brainstorm)* — `other_costs=200` from nowhere

## Intervention Points

### Prevention
- Reconcile `validate_budget` arguments against the flight and hotel results in the tool log and
  the extracted `days` before the verdict is used.
- Treat any argument with no source in a tool result as ungrounded — this is comparison, not
  judgement.

### Detection
- Reconcile every monetary figure in the itinerary against the tool log.
- Treat an unchanged budget-fabrication rate under an active gate as evidence that the gate is
  not firing, since the baseline behaviour is deterministic.

### Mitigation
- Where the total cannot be computed from tool results and the real duration, do not present a
  budget verdict as verified at all. The harm is the framing, so hedging it does not remove it.
- Regenerate cost figures against the real prices in the log, which are present and usable.

### Recovery
- Retain the tool log alongside the itinerary so unsupported verdicts can be identified later.

---

## Management Plan

[Not yet developed. Run failure management to develop a plan for this failure mode.]
