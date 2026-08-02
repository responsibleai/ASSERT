# Requirements

## R1 — Every concrete trip fact must be traceable to a tool result

Flight numbers, times, fares, hotel names, nightly rates, and weather values may
appear in a reply only if a tool returned them in this conversation.

Traceability is checked against a **grounding ledger** accumulated across the
whole conversation, not against the current turn's tool output. This is what
makes R1 survive F4.

## R2 — Coverage gaps must be reported, not filled

When the user asks about a domain (flights / hotels / weather) for which the
ledger holds nothing, the agent must say so plainly and continue being useful in
the parts it can support.

This is the requirement that actually addresses F1. F1 is a coverage-gap failure,
so the fix has to make the gap *visible and speakable* rather than merely
punishing the model for filling it.

## R3 — Detection must be deterministic before it is semantic

A claim is treated as ungrounded when a sentence contains **both** a domain cue
(flight / hotel / weather vocabulary) **and** a concrete token (flight number,
time, price, proper-noun hotel name) that is absent from the ledger.

Requiring both signals *in the same sentence* is what keeps R3 from firing on
legitimate general reasoning. "Mornings are usually cheaper" has the domain cue
but no concrete token. "Your flight AA118 departs 7:45am" has both.

The deterministic check runs first because it cannot fail open and costs nothing.

## R4 — Research tools are never gated

`search_flights`, `find_hotels`, and `check_weather` execute unconditionally.

This directly overrides the generated `pre_tool_call` policy (F6). Gating
retrieval to prevent fabrication is self-defeating: it removes the grounding
whose absence causes the fabrication. The guarded agent documents this override
at `_pre_tool_allows` rather than silently ignoring the generated rule.

## R5 — The semantic annotator is a backstop and fails open

The LLM annotator returns an object of six independent boolean flags (one per
fabrication category — the contract the generated Rego expects).

It runs *after* the deterministic check and can only add findings. If it errors,
times out, or returns an unparseable shape, the deterministic verdict stands.
Governance must never become a new source of failure.

## R6 — Regeneration must preserve everything legitimate

When a reply is blocked, the rewrite keeps every grounded detail, every piece of
useful reasoning, and every genuinely helpful suggestion. Only the unsupported
concrete claims are replaced — with an explicit statement of what was not
retrieved and how to get it.

Baseline scenario overrefusal is already 24.0%. A fix that trades policy
violations for refusals has not improved the agent.

## R7 — Grounded and ungrounded content must be distinguishable

Addresses F5. The rewritten reply must make clear which parts rest on retrieved
data and which are general reasoning, so a partly-grounded itinerary is partly
trustworthy rather than uniformly suspect.

## R8 — The clarification branch must announce its own blindness

Addresses F2. When routing lands on the toolless `clarification` node, the agent
must be told, in-context, that it holds no retrieved data — so that the absence
of grounding is a fact it can state rather than a hole it silently fills.
