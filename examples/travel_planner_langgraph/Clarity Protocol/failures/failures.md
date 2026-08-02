# Failure Modes

Ordered by measured contribution to the baseline scores.

---

## F1 — Fabricated concrete trip details (PRIMARY)

**Observed:** policy_violation 20.0% prompts / **60.0%** scenarios.

The agent states flight numbers, departure/arrival times, fares, hotel names,
nightly rates, and weather forecasts that no tool returned. Presented in the same
prose and the same confidence as retrieved details.

**Mechanism:** a coverage gap. The agent is asked for a complete itinerary, holds
partial data, and fills rather than reports the difference.

**Why it is severe:** these details are executed, not read. The harm is realized
after the conversation is closed.

---

## F2 — The toolless clarification branch

`route_after_intent` routes everything not classified `book_trip` to
`clarification`, which has no tools bound. The model is asked about flights,
hotels, and weather with zero retrieved data in hand.

Every concrete detail produced in this branch is fabricated by construction. This
is not a tendency, it is an identity: there is no grounding available to be
faithful to.

This is the single largest structural contributor to F1, and it is invisible to
the model — nothing tells it that it entered a branch where it cannot know
anything.

---

## F3 — Single-round research

`research` performs exactly one tool round. If the user asks about four cities
and the round covers two, the other two are never retrieved and never retried.

The model receives no signal that coverage was partial. It sees a tool result and
reasonably reads it as *the* answer rather than *part of* the answer.

---

## F4 — Monotonic drift across turns

The 3x prompt-to-scenario gap is the signature. Each turn expands the itinerary
surface the agent has committed to while the grounding set stays frozen. Late
turns are therefore near-guaranteed to assert beyond the data, and earlier
fabrications get restated as though they were established facts.

Any fix that inspects only the current turn will miss this.

---

## F5 — Uniform presentation of grounded and ungrounded claims

Even where the agent is partly right, the user cannot tell which parts. Grounded
and invented details share one voice and one paragraph.

This makes F1 worse than its rate suggests: a 60% scenario violation rate on
output that carries no reliability signal means the *whole* itinerary is
untrustworthy, not 60% of it.

---

## F6 — Generated pre-tool-call rules gate the wrong direction (FRAMEWORK)

`acs generate` emitted `pre_tool_call` rules gating `search_flights` and
`check_weather` on the fabrication annotator.

This is backwards. Retrieval is the *cure* for fabrication, not a vector for it.
Blocking the research tools when fabrication risk is high starves the agent of
exactly the grounding that would prevent the failure, and would convert F1 into
a much larger F4 plus an overrefusal spike.

Recorded as a framework finding. The guarded agent deliberately allows these
tools unconditionally; see `_pre_tool_allows`.

---

## Non-failures

Deliberately excluded, to keep the atomic behavior clean and avoid the
overrefusal trap seen in other domains this batch:

- **Reasoning about travel without concrete claims** ("mornings are usually
  cheaper for that route") — general knowledge, not a checkable fact.
- **Explicitly hypothetical planning** ("if the flight lands by 3pm, you could...").
- **Asking clarifying questions.**
- **Restating what the user themselves supplied.**
