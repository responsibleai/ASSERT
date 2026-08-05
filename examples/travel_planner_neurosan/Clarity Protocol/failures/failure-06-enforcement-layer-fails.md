# Failure: The enforcement layer itself fails

## Summary

The output gate has five failure modes of its own, and three of them are specific to this
pipeline in ways that matter.

**Suppressed advisories.** The obvious fix for wrong-country entry requirements is to remove them
when they cannot be attributed to the destination. The traveller then reads silence as "nothing
required" and travels without a visa — the exact harm the requirement exists to prevent,
reintroduced by its own remedy.

**Silent no-op.** The advisory check needs a host-dispatched semantic annotator, whose name must
match in three places: the manifest key, `input.annotations.<name>` in the Rego, and the
dispatcher branch. A mismatch in any one makes the gate pass everything while reporting
enforcement as active. Nothing errors.

**Decorative marking.** A caveat is attached but the headline still reads "$1,820 total, within
budget". The traveller reads the number and skips the qualifier, so the transcript changes and
the belief does not.

**Over-marking**, which hedges the itinerary into uselessness and sends the traveller to an
unmoderated search engine — improving the harm metric while leaving real exposure unchanged.

**Baseline drift**, where the guarded variant "fixes" the hardcoded `validate_budget` arguments or
the shared advisory payload instead of gating the output. This invalidates the A/B, and in the
`_tools.py` case propagates to every other demo that imports it.

## Failure Chain

1. Enforcement is enabled. The itinerary is evaluated against `run_pipeline`'s tool log.
2. **Branch A — suppression.** The gate removes entry requirements it cannot attribute.
   3. The itinerary contains no visa information. The traveller concludes none is needed.
      **harm begins** — identical to the ungoverned failure, now caused by the control.
      - *Intervention point (prevention):* Never suppress advisories. Mark them unverified at the
        point they appear and direct the traveller to an authoritative source.
3. **Branch B — silent no-op.** The annotator name does not match across manifest, Rego, and
   dispatcher.
   4. Every claim passes. The governed run reports enforcement active and produces metrics
      indistinguishable from a well-behaved gate. **harm begins**, and it is now invisible.
      - *Observation:* The budget fabrication is deterministic, which makes this diagnosable: a
        governed run that does not move that number is a gate that is not firing, not a clean
        baseline. This domain has an unusually strong sentinel for under-enforcement — it should
        be used.
      - *Intervention point (detection):* Verify the gate fires by inspecting a governed
        trajectory for the invariant total, rather than by reading the aggregate metric.
4. **Branch C — decorative marking.** The unsupported figure is marked but still stated as a
   headline.
   5. The traveller reads "$1,820, within budget" and books. **harm begins** — unchanged from the
      baseline, while the metric records a mitigation.
      - *Intervention point (mitigation):* Where a budget verdict cannot be computed from tool
        results and the real duration, do not state it as verified at all. Its harm is the
        verification framing, so hedging does not remove it.
5. **Branch D — over-marking.** So many claims are qualified that the itinerary is unusable.
   6. The traveller abandons it for an unmoderated source. **harm begins** — real exposure is
      unchanged or worse, and the harm metric has improved.
      - *Intervention point (prevention):* Regenerate against the real prices in the log rather
        than marking. The correct figures are available, so grounding is achievable and hedging is
        rarely necessary.
      - *Intervention point (detection):* Measure legitimate-planning preservation alongside harm
        reduction; neither is interpretable alone.
6. **Branch E — baseline drift.** The guarded variant edits `agent.py`'s hardcoded arguments or
   `phoenix_auto_trace/_tools.py`'s advisory payload.
   7. The A/B compares two different systems and its result means nothing. A `_tools.py` change
      additionally propagates to every other demo importing it, including a sibling travel planner.
      - *Intervention point (prevention):* Change nothing outside the guarded module. The
        `run_pipeline` seam exists precisely so nothing else needs to move.
7. **Branch F — fail closed.** An evaluator error produces no itinerary at all, and the traveller
   goes elsewhere. Worse than an imperfect plan.
   - *Intervention point (prevention):* Fail open to the ungoverned itinerary.

## Observations

- **Severity:** High — Branches A, C, and D each reproduce the original harm through the control
  intended to prevent it, while improving the measured result. Branch B is the most insidious,
  since it manufactures confidence with no enforcement at all; it is also the most diagnosable
  here, because the deterministic budget fabrication provides a reliable sentinel. Branch E
  silently invalidates every number produced.
- **Related failures:** Determines whether *Fabricated budget verification* and *Entry
  requirements for the wrong destination* are actually mitigated. Branch A is the direct
  reintroduction of the latter. Branch D is the countervailing force to every prevention proposed
  elsewhere, which is why harm reduction and legitimate-planning preservation must be reported as
  a pair.
- **Variants:**
  - Suppressed advisory reads as none required *(brainstorm)* — Branch A
  - Annotator name mismatch silently no-ops *(brainstorm)* — Branch B
  - Caveat ignored while headline figure stands *(brainstorm)* — Branch C
  - Over-marking hedges itinerary into uselessness *(brainstorm)* — Branch D
  - Guarded variant edits the baseline itself *(brainstorm)* — Branch E

## Intervention Points

### Prevention
- Never suppress advisories; mark them unverified and point to an authoritative source.
- Do not state an ungroundable budget verdict as verified in any form.
- Regenerate figures against the real prices in the log rather than hedging.
- Change nothing outside the guarded module — not `agent.py`, not `phoenix_auto_trace/_tools.py`.
- Fail open on evaluator error.
- Match the annotator name exactly across manifest, Rego, and dispatcher.

### Detection
- Use the deterministic budget fabrication as a sentinel: a governed run that does not move it is
  a gate that is not firing.
- Verify enforcement by inspecting governed trajectories, not by reading aggregate metrics.
- Measure harm reduction and legitimate-planning preservation together.

### Mitigation
- Bound the amount of hedging so the itinerary stays usable.

### Recovery
- Keep policies declarative and reviewable so the boundary can be retuned without touching the
  pipeline.

---

## Management Plan

[Not yet developed. Run failure management to develop a plan for this failure mode.]
