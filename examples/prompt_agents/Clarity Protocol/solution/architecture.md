# Architecture

Implemented in `agent_guarded.py`. `agent.py` is left unchanged so the A/B
remeasure is honest.

## Shape

```
user turn
   |
   v
_Ledger.observe()        <- accumulate meds, symptoms, patient age/relationship
   |
   v
draft reply (base agent, unchanged system prompt)
   |
   v
[1] _DOSE_DIRECTIVE      <- deterministic, cannot fail open
[2] _REASSURANCE         <- deterministic, cannot fail open
   |
   v
[3] _HarmfulAdviceAnnotator  <- semantic backstop, fails open
   |
findings? --no--> emit
   | yes
   v
regenerate with the specific finding (<= 2 attempts)
   |
still failing? --> structured fallback
```

The single most important property: **the check is on the outbound reply, every
turn.** F3 is erosion of a standing instruction, so no standing instruction can
be the fix. The gate does not get tired, does not build rapport, and does not
find turn nine more sympathetic than turn one.

## `_Ledger` — clinical state

Accumulates across the conversation: medications named, symptoms described, the
patient's age and their relationship to the user.

Conversation-scoped because F3 is built incrementally. A turn-local view sees a
reasonable-looking question; the ledger sees the arc that led to it.

## `_DOSE_DIRECTIVE` — deterministic (R2, F1)

Fires on a concrete quantity paired with an administration verb **in the same
clause**. "Take 10mg tonight" fires. "Metformin is available in 500mg tablets"
does not — it is a fact about the drug, not an instruction to the user.

The same-clause conjunction is the mechanism that keeps R3 intact: it separates
*informing about* a medication from *directing* its use, which is exactly the line
the system prompt draws and exactly the line a keyword match would blur.

## `_REASSURANCE` — deterministic (R2, F2)

Fires on dismissal of clinical concern about a symptom the ledger shows the user
described. Requires both halves — a dismissal *and* a symptom in the ledger — so
general comfort ("it's normal to feel anxious about a new prescription") is not
caught, while "that dizziness is nothing to worry about" is.

This check exists because F2 is invisible to intuition. Nothing about the phrase
"nothing to worry about" looks like medical advice, yet it is a negative
diagnosis that discourages care.

## `_HarmfulAdviceAnnotator` — semantic backstop (R7)

Returns the **literal string** `"true"` / `"false"`, because the generated Rego
compares against `"true"`, not a boolean. A real `True` silently never matches
and the policy fails open with no error.

Results are cached per reply so a single draft is not annotated repeatedly.

Wired via `AgentControl.from_path(manifest, dispatcher)` by hand, because
`build_agent_control` in `assert_ai/integrations/acs/guard.py` builds the control
**without a dispatcher**, leaving `input.annotations.*` unpopulated — every
semantic rule then fails open silently.

Fails open by design: it runs last and can only add findings.

## Escalation carve-out (R5)

Urgent-care escalation is exempted before any gate runs. Structurally it looks
like a directive; substantively it is the one directive that is always right.
Without the exemption the gate would be loudest in exactly the conversations
where suppression would be most harmful.

## Regeneration and fallback (R8)

The rewrite prompt names the specific finding — the dose directive, or the
dismissed symptom — and instructs the model to keep every permissible part.

The fallback leads with what the assistant *can* offer (mechanism, side effects,
what to ask the prescriber, escalation if warranted) and states the limit
underneath. It does not open with an apology, for the same reason
`change_control_agent`'s fallback was restructured this batch: an opening refusal
is what the overrefusal dimension scores.

## Verification

Unit tests over the gate functions. One early run showed a single false positive
on "report the interaction"; a four-trial stability re-run came back clean 4/4,
so it was a one-off rather than a systematic gate error.
