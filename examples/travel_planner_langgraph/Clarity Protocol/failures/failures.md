# Failure Modes

1. **[Fabricated trip costs](failure-01-fabricated-trip-costs.md)** (Critical) The
   planner states airfares, nightly rates, availability, totals, and budget
   confirmations that no tool returned. `itinerary_optimizer` has no tool access and is
   bound only by a "never fabricate" prompt line, so when retrieval is empty, partial,
   errored, or skipped it composes an equally complete plan with generated figures
   formatted identically to real ones. Travellers budget and book against numbers that
   do not exist, often irreversibly; operations cannot attribute the error afterwards.
   Triggered by empty results, classifier misroute, and sustained budget pressure
   across turns. **no mitigation plan**
2. **[Invented entry requirements](failure-02-invented-entry-requirements.md)** (Critical)
   The planner asserts visa, entry, and health requirements that
   `check_travel_advisories` never returned — or silently omits ones it did return. The
   traveller arrives without a required document and is refused boarding or entry,
   losing the whole trip. Unlike a wrong price this cannot be resolved by spending
   more, and entry rules change faster than model knowledge, so parametric answers are
   stale even when not invented. Carries duty-of-care exposure for the operator.
   **no mitigation plan**
3. **[Confirmed and invented facts are indistinguishable](failure-03-provenance-collapse.md)** (High)
   Retrieved and generated content are rendered in one uniform voice with no
   provenance marking. Travellers cannot tell which claims to verify, and operations
   cannot attribute a wrong claim after a complaint. Causes no direct harm itself but
   sets the recurrence rate of every other mode by preventing both in-the-moment
   defence and after-the-fact diagnosis. **no mitigation plan**
4. **[Poisoned tool output hijacks the plan](failure-04-poisoned-tool-output.md)** (High)
   Tool results enter context as plain text with no data/instruction boundary,
   so attacker-controlled listing or advisory text can direct the model — promoting a
   property, altering a total, or suppressing a safety advisory. Delivered in the
   planner's own trusted voice. Repeatable and silent, and the suppression variant
   turns a commercial manipulation into a physical-safety failure.
   **no mitigation plan**
5. **[The grounding check itself fails](failure-05-grounding-check-fails.md)** (High)
   The enforcement layer's own failure modes, all sitting on one tuning boundary: too
   broad and it suppresses legitimate qualitative answers until operators disable it;
   too narrow and fabrications pass while the check's existence manufactures unearned
   trust; and repeated denial can loop or degrade to an empty answer. Each branch
   either negates the benefit or leaves the system worse than the unguarded baseline.
   **no mitigation plan**
6. **[Missing trip details are assumed rather than asked about](failure-06-assumed-trip-parameters.md)** (Medium)
   When destination, dates, or budget are absent, the classifier treats the
   request as complete and the planner supplies the missing values itself, planning
   against its own assumptions without flagging them. Direct harm is usually wasted
   effort, but assumed parameters feed straight into the cost and advisory claims the
   traveller then acts on. **no mitigation plan**

## Cross-Cutting Patterns

**The composition boundary is the pinch point.** Failures 01, 02, 03, and the detection
half of 04 all have an intervention point at the same moment: after the plan is
composed and before it reaches the traveller, comparing the claims in the draft against
the structured record of what the tools actually returned. One mechanism placed there
addresses four failure modes. This is the strongest architectural signal in the
analysis, and it argues for enforcement on the outgoing message rather than on tool
calls — the harm is an assertion made by a node that issues no tool calls at all, so
there is no call to intercept.

**"No retrieval occurred" is a shared trigger.** Failures 01 and 02 both reach their
worst form through the same condition: the graph produced a plan without the relevant
lookup having run. A single upstream check — did this planning turn actually reach
retrieval, and did retrieval return usable data — collapses the maximal variant of both
modes. The graph currently has no branch for insufficient data; it proceeds to
composition unconditionally.

**Cascade: 06 → 01/02.** Assumed parameters are not merely a usability problem. An
assumed budget becomes a false budget-validation claim and assumed dates become
fabricated fares, so the Medium-severity routing failure seeds the two Critical ones.

**Amplification: 03 governs the persistence of everything else.** Provenance collapse
is the terminal step of both Critical chains. Without it the failures would be
detectable and correctable; with it they recur indefinitely.

**Countervailing pressure: 05 is the cost of fixing 01 and 02.** The enforcement that
resolves the grounding failures introduces its own. Notably, failure 05 Branch A
(over-broad suppression) and failures 01/02 pull in opposite directions, which means
neither can be evaluated alone. Any measurement of this system must report harm
reduction and suppression of acceptable behaviour as a paired result — a drop in
fabrication bought with a rise in evasive non-answers is not an improvement.

**Advisory suppression has two independent routes.** Failure 02 reaches it by omission
from an ungrounded composition; failure 04 reaches it by adversarial instruction in
retrieved text. A fix for one does not cover the other, so retrieved advisories should
be treated as mandatory output independent of any downstream reasoning.
