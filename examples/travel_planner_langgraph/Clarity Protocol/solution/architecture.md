# Architecture

Implemented in `agent_guarded.py`. The ungoverned `agent.py` is unchanged so the
A/B remeasure is honest.

## Shape

```
user turn
  |
  v
classify_intent  (unchanged)
  |
  +-- book_trip --> _guarded_research --> tools (UNGATED) --> ledger.record()
  |                        |
  +-- else -----> clarification (annotated as ungrounded)
                           |
                           v
                  draft reply
                           |
                  [1] _structural_gap   <- deterministic, cannot fail open
                           |
                  [2] _FabricatedDetailsAnnotator  <- semantic, fails open
                           |
                     findings? --no--> emit
                           | yes
                           v
                  regenerate with gap report (<= 2 attempts)
                           |
                     still failing? --> structured fallback
```

## `_Ledger` — the grounding record

The load-bearing component. Every tool result is decomposed into the concrete
tokens it actually establishes: flight numbers, times, fares, hotel names, rates,
weather values.

The ledger is **conversation-scoped, not turn-scoped**. This is what defeats F4:
a claim made in turn 6 is checked against everything retrieved in turns 1-6, so
a detail legitimately retrieved early can still be restated later, while a detail
never retrieved stays ungrounded no matter how many turns have passed since it
was invented.

## `_asserted_domains` / `_structural_gap` — deterministic detection

Implements R3. A sentence is flagged only when it carries a domain cue **and** a
concrete token that is not in the ledger.

The both-signals-same-sentence rule is deliberate. Domain cue alone flags every
sensible generalization about travel; concrete token alone flags dates and prices
the user themselves supplied. The conjunction is what separates "invented a
flight number" from "knows how airports work" — and it is the reason this design
expects to avoid the overrefusal blowup that hit `change_control_agent` when its
gate caught adjacent legitimate work.

`_structural_gap` additionally reports domains the user asked about for which the
ledger holds nothing at all. That output feeds the regeneration prompt, which is
how R2 turns a silent hole into a stated one.

## `_FabricatedDetailsAnnotator` — semantic backstop

Returns an object with six independent booleans, matching the contract in the
generated Rego. (Notably the fourth distinct annotator return shape encountered
across five domains in this batch — the generated Rego must be read before the
dispatcher is written, every time.)

Wired via `AgentControl.from_path(manifest, dispatcher)` by hand, because
`build_agent_control` in `assert_ai/integrations/acs/guard.py` constructs the
control **without a dispatcher**, leaving `input.annotations.*` unpopulated so
every semantic rule fails open silently and with no error.

Fails open by design (R5).

## `_pre_tool_allows` — the documented override

Returns allow for `search_flights`, `find_hotels`, `check_weather`
unconditionally, overriding the generated `pre_tool_call` rules.

Kept as an explicit, commented function rather than an omission, so the
disagreement with the generated policy is legible to a reviewer and reportable as
a framework finding (F6).

## Regeneration

Up to two attempts. The prompt carries the specific gap report — which domains
were asked about, which had no ledger entries, which tokens were unsupported —
and instructs the model to keep everything grounded and say plainly what was not
retrieved.

The fallback, if both attempts fail, leads with what *is* grounded, then states
the gaps, then gives the route to fill them. It does not open with an apology,
for the same reason `change_control_agent`'s fallback was restructured: leading
with refusal is what the overrefusal dimension scores.

## Verification

Eight unit assertions over the gate functions, all passing: grounded claims pass,
invented flight numbers are caught, general travel reasoning is not flagged,
user-supplied details are not flagged, and ledger entries from earlier turns keep
later restatements legal.

## Measured result

Two governed revisions were run against the same 50-row suite (25 prompt,
25 scenario), judged on the built-in `policy_violation` and `overrefusal`
dimensions.

| run | PV prompt | PV scenario | OR prompt | OR scenario |
|---|---|---|---|---|
| baseline | 20.0% | 60.0% | 0.0% | 24.0% |
| governed v1 | 4.0% | 56.0% | 0.0% | 48.0% |
| **governed v2** | 16.0% | **28.0%** | 0.0% | 40.0% |

v1 bought a large drop in prompt-level fabrication but barely moved the
scenario rate and doubled overrefusal. Reading the overrefusal justifications
showed why: the guard was blocking *generic planning help that needs no
grounding at all* — comparison frameworks, budget-split formulas, neighbourhood
descriptions, and search templates. The hard-coded `grounded_summary()` fallback
was emitting "tell me your dates and I will look it up" whenever the ledger was
empty, which is the single most common multi-turn state.

v2 rewrote the fallback, broadened the annotator carve-out, and gave the
regeneration prompt an explicit "what you must still deliver" block. Scenario
fabrication fell 60.0% -> 28.0% (-32 points against baseline, -28 against v1)
and overrefusal came down 48.0% -> 40.0%.

v2 dominates v1 on every axis. The residual 40% scenario overrefusal is the
remaining cost: the guard is still conservative in long multi-turn threads where
the user pushes for specifics the tools never returned. That is the correct
direction to be wrong in for this behavior, but it is not free, and it is the
same bleed pattern observed in `change_control_agent` v1, `azure_doc_qa`, and
`science_research_agent`.

**The cross-cutting lesson, confirmed here for the second of four times: a guard
must be scoped to the harmful substance, not to the topic that contains it.**
Blocking "travel specifics" blocks travel help. Blocking "unsourced travel
specifics" blocks only the failure.
