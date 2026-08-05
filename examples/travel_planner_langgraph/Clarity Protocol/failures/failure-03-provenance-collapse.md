# Failure: Confirmed and invented facts are indistinguishable

## Summary

`itinerary_optimizer` renders retrieved values and generated values in one uniform
voice. Even when most of a plan is properly grounded, nothing in the output tells the
traveller which lines came from a tool and which the model supplied. This is not a
root cause — it is the amplifier that converts every other fabrication mode from a
detectable, correctable error into an invisible, recurring one.

The **traveller** loses the ability to defend themselves: with no signal about which
claims warrant checking, they must either verify everything (defeating the purpose of
the planner) or verify nothing (accepting whatever was invented). **Travel operations**
lose the ability to triage: after a complaint they cannot determine whether a wrong
figure came from the tool or the model, so the root cause is never identified and the
same failure recurs for other travellers.

## Failure Chain

1. A plan is composed mixing tool-sourced values with model-generated ones.
   - *Observation:* This is the normal case, not an edge case. Partial retrieval is
     routine, so most plans are mixtures.
2. All content is rendered in a single uniform format with no provenance annotation.
   - *Intervention point (prevention):* Carry provenance through composition and
     surface it — mark confirmed values distinctly from estimated ones.
3. The traveller reads the plan with no basis for differential trust. **harm begins**
   — the harm at this step is the loss of the traveller's ability to protect
   themselves, which is realised the moment they act on any part of the plan.
   - *Branch point:* A diligent traveller verifies everything, and the planner has
     delivered negative value — it cost them more effort than planning unaided.
   - *Branch point:* A typical traveller verifies nothing and is exposed to the full
     severity of whichever fabrication occurred.
   - *Intervention point (mitigation):* Even a coarse confirmed/unconfirmed split
     restores useful differential trust at low cost.
4. A fabricated claim causes concrete harm via one of the other failure modes.
5. The traveller complains. Operations attempt to reconstruct what happened.
   - *Intervention point (detection):* A retained per-turn provenance record makes
     attribution immediate and turns an unanswerable complaint into a fixable bug.
6. Because the response contains no provenance and the retrieval record is not
   retained alongside it, operations cannot attribute the error.
7. The complaint is settled as a one-off. No fix is made. **harm ends** for this
   traveller.
8. The identical failure recurs for other travellers indefinitely, because the signal
   needed to detect the pattern was never captured.
   - *Observation:* This step is why the failure is rated High despite causing no
     direct harm itself — it sets the recurrence rate of every other mode.

## Observations

- **Severity:** High — No direct harm in isolation, but it removes both the
  traveller's in-the-moment defence and the operator's after-the-fact diagnosis. It
  is the mechanism by which every other failure mode becomes persistent rather than
  one-off.
- **Related failures:** Amplifies *Fabricated trip costs* and *Invented entry
  requirements* — step 9 of the costs chain and step 9 of the entry chain both
  terminate here. Also interacts with *Policy misses a fabrication*, where the absence
  of provenance means a false negative in enforcement is equally undiagnosable.
- **Variants:**
  - Confirmed and invented facts are indistinguishable in the final itinerary
    *(brainstorm)*

## Intervention Points

### Prevention
- Thread provenance from the retrieval node through composition so it survives into
  the rendered prose.
- Require the composition step to distinguish confirmed values from estimates rather
  than normalising both into the same register.

### Detection
- Retain the structured retrieval record alongside the emitted response so any claim
  can be attributed after the fact.

### Mitigation
- Surface at minimum a binary confirmed/unconfirmed marker per load-bearing claim —
  enough for the traveller to know what to check without cluttering the plan.

### Recovery
- Provenance logs let operations answer a complaint precisely and identify systemic
  patterns across complaints instead of treating each as isolated.

---

## Management Plan

[Not yet developed. Run failure management to develop a plan for this failure mode.]
