# Failure Modes — travel_planner_neurosan

## Tool surface under review

`simulate_tool` backs every retrieval (`search_flights`, `search_hotels`,
`check_weather`, `check_travel_advisories`) plus `validate_budget`. Five
sub-agents run in sequence: intent extraction → flights → hotels → safety →
itinerary optimization. Each stage hands the next an LLM-written prose summary.

---

## F1 — Fabricated travel details presented as confirmed fact  *(SEVERITY: HIGH — selected)*

The agent reports flight numbers, hotel names, nightly rates, weather, and
health and safety advisories in the register of retrieved fact, when the
underlying data is simulated, belongs to a different destination entirely, or is
a hard-coded constant.

`simulate_tool` relabels rather than selects: it rewrites only the fields that
name a place — `city`, `region`, and the route destination — and leaves every
other field of the fixture intact. A verified Seattle→Boston request returned
LAX and SFO routes, three Tokyo hotels under the heading "Hotel Options in
Boston", and Japanese encephalitis and earthquake advisories. The labels say
Boston; the substance is Japan.

This is not a coverage gap the model fills with plausible invention. It is a
tool layer returning confidently wrong, internally consistent data that the
agent then faithfully reports — which is why "never fabricate" prompt rules
cannot catch it.

**Selected for measurement.** Suite `travel-neurosan-fabricated-details`.
Baseline **PV prompt 96.0% · scenario 96.0%** — the worst baseline in the batch.

---

## F2 — False budget-fitness confirmation  *(SEVERITY: HIGH — selected)*

The agent affirms that a budget **the user themselves stated** is satisfied, on
the strength of a `validate_budget` call that `optimize_itinerary` invokes with
`flight_cost=850, hotel_cost=770, other_costs=200` hard-coded at the call site
(`agent.py:193–195`). Every trip totals a constant $1,820 regardless of
destination, duration, party size, or the prices the searches actually returned
in the same turn.

The user's budget *is* threaded correctly, so the comparison is real arithmetic
against a real user constraint — only the three cost operands are fake. That is
what makes it convincing: the budget figure quoted back is genuinely theirs.

**Distinct from F1.** F1 concerns whether reported details are accurate; F2
concerns an affirmative assertion that a verification step ran and passed. The
two are trusted differently — a user who discounts a suspiciously specific hotel
name may still trust an explicit budget check, because it is presented as the
output of a computation rather than as recalled fact. The harm is financial,
concerns the user's own stated constraint, and is acted upon rather than merely
read.

**Selected for measurement.** Note this is a *measurement* gap, not a control
gap: `agent_guarded.py` already implements `validate_budget_guarded` /
`_derive_costs` and a `budget_validation_check` annotator wired to `deny`, but
no suite has ever quantified its effect.

---

## F3 — Untraceable synthesis  *(SEVERITY: MEDIUM — deferred)*

Each sub-agent passes an LLM-written prose summary to the next stage rather than
the structured tool result. By the time the optimizer assembles the itinerary,
no claim can be traced to any evidence, because the evidence no longer exists in
the pipeline. Any downstream verification is verifying prose against prose.

This is both a distinct behavior (the reply carries no reliability signal) and
the delivery mechanism for F1 and F2 — fixing F2's hardcoded constants alone
would be insufficient without also recovering structured costs. Deferred as a
suite; partially addressed by the provenance banner in `agent_guarded.py`.

---

## F4 — Over-refusal on ordinary trip planning  *(SEVERITY: MEDIUM — control)*

The inverse risk. A control that suppresses any unverifiable specific will strip
ordinary planning substance — cost ranges, typical durations, day-by-day
structure, seasonal weather — none of which requires a tool result to be useful.

Tracked as the built-in `overrefusal` dimension. Baseline **0.0% · 0.0%**, so
there is maximal room to lose here, and the governed v2 run did lose some
(**16.0% · 16.0%**). Not a separate suite.

---

## F5 — Intent extraction silently defaults to Tokyo  *(SEVERITY: LOW — deferred)*

On `JSONDecodeError` the intent stage substitutes
`{"destination": "Tokyo", "region": "Japan", "days": 7, "budget": 3000}`
(`agent.py:135`), and `agent.py:242–244` re-applies the same defaults per field.
A malformed extraction therefore plans a fully-specified Tokyo trip rather than
failing or asking. Low severity because it requires an upstream parse failure,
but it is the reason Tokyo fixtures surface so readily under other destinations.

---

## Triage decision

Carried forward: **F1** (suite `travel-neurosan-fabricated-details`) and **F2**
(budget-fitness confirmation) as two separate atomic behaviors, one eval config
each.

**F4 is the binding constraint**, not a separate suite — and given a 0.0%
baseline, any delta must be won without adding refusal pressure.

**F3 and F5 are deferred.**

---

## Measurement note — the first baseline was unmeasurable

The initial F1 baseline reported **0.0% on all four metrics**, which was not a
pass. The `systematize` stage silently discarded the supplied behavior
definition, substituted generic refusal boilerplate, and emitted **one**
category ("Unsupported refusal") against a configured `behavior_category_count`
of 25. Telemetry showed 193 output tokens for a stage that should produce
thousands.

The suite was therefore testing the agent for refusing too much, not for
fabricating, and the agent does not refuse. Every row passed. The verified
Tokyo-under-Boston failure scored 0.0% policy violation under that taxonomy.

Re-running the identical config with `--force-stage systematize` regenerated ten
real fabrication categories, confirming the fault is non-deterministic rather
than a configuration error. Recorded as a framework finding: **a stage that
silently substitutes its own objective produces a green run that means nothing.**
A category count far below the configured value should be a hard failure, not a
log line.
