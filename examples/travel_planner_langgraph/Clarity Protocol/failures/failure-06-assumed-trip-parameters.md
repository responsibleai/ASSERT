# Failure: Missing trip details are assumed rather than asked about

## Summary

When destination, dates, or budget are absent, `intent_classifier` should route to the
`clarification` node. When it instead judges the request complete, the planner silently
supplies the missing parameter — choosing dates, assuming a budget, picking an
interpretation of an ambiguous destination — and plans against its own assumption. The
traveller receives a confident, complete plan for a trip they did not describe.

The **traveller** wastes time on an irrelevant plan, and in the worst case acts on it
before noticing the substitution. The failure also silently converts a routing decision
into a fabrication: an assumed budget flows into a budget-satisfaction claim, and
assumed dates flow into fare and availability claims, seeding the higher-severity
grounding failures with parameters the traveller never supplied.

## Failure Chain

1. The traveller sends a short or underspecified request.
   - *Observation:* This is extremely common — natural phrasing omits dates far more
     often than it includes them.
2. `intent_classifier` judges the request complete enough for planning and routes to
   `research` rather than `clarification`.
   - *Intervention point (prevention):* Require the presence of specific named
     parameters before the planning path may be taken, rather than relying on a
     holistic completeness judgment.
3. Retrieval runs against assumed parameters, or is skipped for parameters that were
   never determined.
   - *Observation:* Retrieval against an assumed date returns real data for the wrong
     trip, which is more convincing and therefore more misleading than no data.
4. `itinerary_optimizer` composes a plan, filling remaining gaps with plausible values.
   - *Intervention point (detection):* Compare the parameters used in composition
     against those actually supplied by the traveller; any difference is an assumption
     that must be surfaced.
5. The plan is presented without flagging that key parameters were assumed.
   **harm begins**
   - *Intervention point (mitigation):* State assumptions explicitly at the top of the
     plan and invite correction.
6. **Branch point:** The traveller notices the wrong dates or budget — harm is limited
   to wasted time and reduced trust. Or they do not notice, and the assumed parameters
   feed the cost and advisory claims they subsequently act on.
7. The traveller either restates their requirements or books against parameters they
   never chose. **harm ends** at correction, or escalates into the fabricated-costs
   and invented-entry-requirements chains.

## Observations

- **Severity:** Medium — Direct harm is usually limited to wasted effort and a poor
  first impression, and the traveller is reasonably likely to notice a wrong date. It
  is rated Medium rather than Low because of its role as an upstream feeder: an assumed
  budget becomes a false budget-validation claim, and assumed dates become fabricated
  fares and availability.
- **Related failures:** Upstream trigger for *Fabricated trip costs*. Shares a root
  cause with the misroute variant of that mode — both are `intent_classifier` making an
  unverified routing decision with no downstream check that the route was correct.
- **Variants:**
  - Missing trip details are assumed, not asked about *(brainstorm)*

## Intervention Points

### Prevention
- Gate the planning path on the presence of specific required parameters rather than a
  holistic judgment of completeness.
- Make `clarification` the default for ambiguity instead of the exception.

### Detection
- Diff the parameters used in composition against those the traveller actually stated.

### Mitigation
- Surface assumptions explicitly in the response and invite correction before the
  traveller acts.

### Recovery
- Preserve stated constraints across turns so a correction does not have to be repeated
  and cannot be silently dropped later in the conversation.

---

## Management Plan

[Not yet developed. Run failure management to develop a plan for this failure mode.]
