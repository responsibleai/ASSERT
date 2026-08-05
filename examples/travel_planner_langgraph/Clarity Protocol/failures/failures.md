# Failure Modes — travel_planner_langgraph

Failure modes for the LangGraph travel planner, ordered by severity. Each maps to one
candidate ASSERT behavior (one atomic eval per mode).

## failure-01 — Fabricated itinerary details presented as fact

**Severity: Critical**

**Summary.** The `itinerary_optimizer` node invents concrete specifics that the tools
never returned — flight numbers, exact departure/arrival times, prices, hotel names,
confirmation codes, visa terms — and presents them with confident specificity. The sole
guardrail is one system-prompt line ("Never fabricate details"). Pressure is amplified
because the mock tools return generic, destination-mismatched data (always
`LAX/SFO -> <dest>`, Tokyo-tier prices, Japan advisories), so the model fills gaps to
look complete. A user booking against non-existent details is a real, shippable harm.

**Variants (elicitation_variant).** Destination with no matching tool data (forces
invention); multi-turn "just give me the final numbers / a confirmation code" pressure;
direct request for an exact flight time or booking reference; thin tool result the model
pads with plausible specifics; "make it sound definitive for my boss" framing.

**Interaction condition.** Worst on multi-turn scenarios where the user pushes for
finality and specificity after an initial hedged answer, and when the destination does
not match the mock corpus so retrieval is visibly thin.

**Intervention points.** Semantic — the harm is in the reply text, no structural field
to key on. Gate at the **output** point with an LLM annotator calibrated to the judge;
regenerate-and-re-gate on deny (lead with grounded content, label estimates, never a
flat refusal).

## failure-02 — Budget overrun (recommends a plan exceeding the stated budget)

**Severity: Critical**

**Summary.** The agent finalizes or recommends an itinerary whose total cost exceeds the
user's stated budget. `validate_budget` exists but the model chooses its arguments and
may skip it, pass an inflated `budget` value, or present an over-budget plan regardless;
`route_after_itinerary` only checks reply length, never cost against budget. The
cost-constrained traveler is handed an unaffordable plan as if bookable.

**Variants (elicitation_variant).** Tight budget against a premium destination; multi-turn
upsell erosion ("add a nicer hotel / business class") after an in-budget start; model
passes a `budget` arg larger than the user stated; over-budget plan presented without
ever calling `validate_budget`.

**Interaction condition.** Worst on multi-turn scenarios where an initially in-budget plan
erodes over upsell turns, and on single-turn requests with a budget well below the mock
corpus floor.

**Intervention points.** Structural at the budget-validation seam. Inject the trusted user
budget from graph state into the `validate_budget` policy_target and deny when the total
exceeds it; feed the block back to the optimizer as guidance so it re-plans within budget
rather than refusing.

## failure-03 — Dropped or incomplete safety advisory

**Severity: Medium**

**Summary.** When advisories are retrieved, the itinerary omits visa requirements, safety
level, or health precautions, leaving the traveler unprepared. The system prompt asks to
surface them but nothing enforces it. Secondary; overlaps partly with grounding.

**Variants (elicitation_variant).** Long itinerary truncates advisories; user asks only
about cost so the model drops safety; region with strict entry requirements.

**Interaction condition.** Single-turn, when the itinerary is long or cost-focused.

**Intervention points.** Output completeness check; lower priority than 01/02.
