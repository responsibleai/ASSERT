# Failure: Fabricated trip costs

## Summary

The planner states specific monetary facts — airfares, nightly rates, room
availability, trip totals, and confirmations that the budget was checked — that no
tool ever returned. `itinerary_optimizer` has no tool access; it composes the final
plan from conversation context and is bound only by a "Never fabricate details" line
in its system prompt. When `research` returned nothing, returned partial data, errored,
or was skipped entirely, the optimizer's job is unchanged and it produces an equally
complete, equally confident itinerary with generated numbers formatted exactly like
retrieved ones.

The **traveller** is harmed financially: they budget, commit, and book against figures
that do not exist, discovering the real cost at the point of purchase or arrival.
**Travel operations** absorb the complaint and cannot determine whether the tool or
the model produced the wrong number. The harm is often irreversible — non-refundable
bookings, committed leave, a trip repriced after the traveller has already paid.

## Failure Chain

1. Traveller requests a trip plan, typically with a stated budget.
   - *Observation:* The budget makes the request higher-stakes: it invites the
     planner to produce numbers that satisfy a target.
2. `intent_classifier` routes the turn. Either it reaches `research`, or it does not.
   - *Branch point:* If routing skips `research`, **no tool runs at all** and every
     figure in the eventual plan is generated. This is the total-fabrication variant.
   - *Intervention point (prevention):* Verify that a planning turn actually reached
     retrieval before permitting a plan to be emitted.
3. Retrieval runs but returns empty, partial, or errored results for one or more of
   flights, lodging, or budget validation.
   - *Observation:* There is no branch in the graph for "insufficient data." The graph
     proceeds to composition regardless.
   - *Intervention point (prevention):* Route insufficient retrieval to the
     clarification/degraded path rather than to composition.
4. `itinerary_optimizer` composes the plan. Missing values are supplied from
   parametric knowledge because the node's instruction to produce a complete itinerary
   is stronger, in practice, than its instruction not to invent.
   - *Intervention point (detection):* Compare each monetary claim in the draft against
     the actual retrieval record for this conversation before the response is released.
5. Generated figures are rendered in the same format as retrieved ones, with no
   provenance marker, and are summed into a stated total.
   - *Intervention point (mitigation):* Mark unconfirmed figures explicitly so the
     traveller knows which lines to verify.
6. Traveller reads the plan as retrieved fact and acts on it — sets a budget, books,
   commits leave, or declines a genuinely cheaper alternative. **harm begins**
7. Reality diverges: the fare is higher, the room is unavailable, or the total exceeds
   the ceiling the plan claimed to satisfy.
   - *Branch point:* Discovered before purchase — recoverable, cost is wasted effort
     and lost trust. Discovered after purchase — financial loss is realised.
8. Traveller rebooks at true prices or abandons the trip. **harm ends**
   - *Intervention point (recovery):* A per-claim provenance record lets operations
     tell the traveller which figures were real, salvaging the grounded portion of the
     plan instead of discarding all of it.
9. Operations receive the complaint. Because confirmed and generated content are
   indistinguishable in the output, they cannot attribute the error, so no fix is made
   and the failure recurs for other travellers.
   - *Observation:* This is where this failure hands off to the provenance failure —
     undiagnosability is what makes it persistent rather than one-off.

## Observations

- **Severity:** Critical — Direct, often irreversible financial harm to the traveller,
  with a plausible path to total trip loss when a fabricated total drives a
  non-refundable booking. Occurs on ordinary, non-adversarial requests, and the
  existing prompt-level prohibition demonstrably does not prevent it.
- **Related failures:** Shares its root mechanism with *Invented entry requirements* —
  both are unsupported claims from a composition node with no retrieval access, and
  both are triggered by the same "no retrieval occurred" condition. Depends on
  *Confirmed and invented facts are indistinguishable* for its persistence: provenance
  collapse is what prevents detection and correction.
- **Variants:**
  - Itinerary states a flight price that no search ever returned *(brainstorm)*
  - Hotel rate and availability are invented *(brainstorm)*
  - Plan claims the budget was validated when `validate_budget` never ran *(brainstorm)*
  - Repeated budget pressure converts an unretrieved price into a stated one
    *(brainstorm)* — multi-turn trigger; `history` replays accumulated pressure while
    the retrieval record does not grow
  - Classifier misroute skips research entirely and the whole plan is invented
    *(brainstorm)* — trigger condition producing the maximal form of this failure

## Intervention Points

### Prevention
- Require evidence that retrieval actually executed before a plan may be composed.
- Route insufficient or failed retrieval to a degraded/clarifying path instead of
  straight to composition.
- Make grounding an evaluated constraint on the produced text rather than an
  instruction to the decoder — the instruction is already present and insufficient.

### Detection
- Compare every monetary and availability claim in the draft response against the
  structured record of what the tools returned this conversation.
- Flag any asserted total that was not produced by `validate_budget`.

### Mitigation
- On a detected unsupported claim, regenerate the response with the violation supplied
  as an explicit constraint, leading with content that *is* supported.
- Mark residual uncertainty as unconfirmed rather than suppressing the whole answer.

### Recovery
- Retain a per-turn provenance record so operations can tell a complaining traveller
  exactly which figures were retrieved and which were not.

---

## Management Plan

[Not yet developed. Run failure management to develop a plan for this failure mode.]
