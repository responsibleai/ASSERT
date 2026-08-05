# Failure: Provenance collapse through the summarization chain

## Summary

Three of the five stages — `search_flights`, `search_hotels`, `check_safety` — call a tool, pass
its raw result to an LLM told to "summarize concisely", and forward only the summary. The
itinerary optimizer never sees raw tool output for any of them. Only `validate_budget`'s JSON
arrives unmediated, and its inputs were fabricated.

This is not a root cause; it is the structural property that makes every other failure here
possible and undetectable at the same time. By the time the harmful claim is written, the
evidence that would contradict it has been discarded one stage earlier.

- Ungrounded prices exist *because* the summarizer dropped the real ones.
- The wrong-country advisory is laundered into fluent prose that no longer looks like a fixed
  payload.
- The fabricated budget verdict passes through with no competing figure to contradict it.

The compression is also uninstrumented: the OTel spans record each summarizer's input and output,
so the loss is technically visible in a trace, but nothing downstream consumes that — the
optimizer cannot know what it was not told.

## Failure Chain

1. A tool returns a complete, correct, structured result.
   - *Observation:* Correct data exists at this point in every failure chain in this system. No
     failure here originates in retrieval.
2. The result is passed to an LLM with the instruction "Summarize the options concisely."
   - *Observation:* Concision is achieved by discarding detail, and the discardable details are
     exactly the ones that matter — prices, option counts, caveats, the fact that a payload was
     generic. The instruction optimises against grounding.
   - *Intervention point (prevention):* Carry raw tool results forward alongside the summary.
3. The summary — lossy, fluent, unattributed — is passed to `optimize_itinerary`.
   - *Observation:* The summary reads with the same confidence as the original, and nothing marks
     what was dropped. The optimizer cannot distinguish "the tool returned no price" from "the
     summarizer omitted it".
   - *Intervention point (detection):* Compare raw tool results against what appears downstream,
     using the log the pipeline already returns.
4. The optimizer composes the itinerary, filling gaps from priors because the evidence is absent.
   **harm begins** — not from any single false claim but from the loss of the ability to tell
   true claims from generated ones.
5. The traveller receives a document in which retrieved and generated content are
   indistinguishable.
   - *Intervention point (mitigation):* Attribute claims to their source so the reader can see
     which parts are grounded.
6. **Branch point — audit.** An operator reviewing the itinerary finds it internally consistent
   and fluent. Nothing signals that its figures came from nowhere.
7. Individual harms end as their trips end. **harm ends** per traveller.
8. The pattern recurs on every run, because nothing surfaces it. The ungrounded-claim rate is
   unmeasurable and therefore unmanaged.
   - *Intervention point (recovery):* Persist the raw tool log with each itinerary so historical
     analysis can quantify what fraction of claims were ever grounded.

## Observations

- **Severity:** High — No direct harm in isolation; it removes the operator's ability to detect
  any other failure and the optimizer's ability to avoid them. It sets the rate of the ungrounded
  cost mode and conceals both Critical modes. Its intervention value substantially exceeds its own
  harm.
- **Related failures:** Direct cause of *Ungrounded cost figures in the itinerary*. Conceals
  *Entry requirements for the wrong destination* by rendering a fixed payload as bespoke prose,
  and *Fabricated budget verification* by removing any competing figure. The reason enforcement
  must ground against `run_pipeline`'s raw log rather than against anything the pipeline passes
  forward internally.
- **Variants:**
  - Summarization chain destroys provenance *(brainstorm)*

## Intervention Points

### Prevention
- Carry raw tool results forward alongside summaries so downstream stages retain something to
  ground against.

### Detection
- Compare raw tool results against downstream claims using the log `run_pipeline` already returns.

### Mitigation
- Attribute claims to their source so retrieved and generated content are distinguishable to the
  reader.

### Recovery
- Persist the raw tool log with each itinerary so the grounded fraction can be measured
  historically.

---

## Management Plan

[Not yet developed. Run failure management to develop a plan for this failure mode.]
