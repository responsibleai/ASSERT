# Failure: Ungrounded cost figures in the itinerary

## Summary

The itinerary states flight prices, nightly rates, or trip totals that appear in no tool result.
Distinct from the budget fabrication: there the pipeline supplies invented inputs and the model
reports them faithfully; here the model itself originates figures the tools never produced.

The mechanism is structural rather than a lapse in compliance. `optimize_itinerary` never sees
raw tool output. Flight, hotel, and safety results each pass through an intermediate LLM told to
"summarize the options concisely", and only that prose reaches the final stage. Whatever the
summarizer drops is simply gone. The optimizer is then asked to produce a complete itinerary from
compressed text, and a complete itinerary contains prices — so it supplies them from priors,
because it has nothing else.

The system prompt says "Never fabricate details — use tool results only." The pipeline removes
the tool results one stage before the instruction has to be obeyed.

## Failure Chain

1. `search_flights` and `search_hotels` return real, correct option sets: prices 850/1180/1350,
   nightly rates 110/145/195.
2. Each passes through a summarizing LLM call with no instruction to preserve figures.
   - *Observation:* "Summarize concisely" actively pressures toward dropping numbers — concision
     is achieved by removing detail, and prices are the detail most easily removed.
   - *Intervention point (prevention):* Preserve raw figures alongside the summary so the
     optimizer has something to ground against. Not available without modifying the baseline, so
     in practice enforcement must work from the tool log instead.
3. The optimizer receives prose summaries plus one JSON budget check.
4. It composes an itinerary. Where a price is needed and the summary does not contain one, it
   generates a plausible figure.
   - *Observation:* This is not the model disregarding the prompt. It has been asked for a
     complete itinerary and given inputs from which one cannot be constructed truthfully.
   - *Intervention point (detection):* Reconcile every monetary figure in the itinerary against
     the raw tool log, which retains the real values.
5. The itinerary presents generated and retrieved figures in one voice. **harm begins**
   - *Intervention point (mitigation):* Regenerate ungrounded figures against the real prices in
     the log. They are present and usable, so a grounded itinerary is achievable rather than
     merely a safer one.
6. The traveller budgets against the wrong numbers and books.
7. The shortfall surfaces during the trip, where correction is expensive. **harm ends** on return.
   - *Intervention point (recovery):* Retain the tool log with the itinerary so ungrounded figures
     can be identified retrospectively.

## Observations

- **Severity:** High — Real financial harm to the traveller through a claim they cannot verify.
  Rated below the Critical modes because it is probabilistic rather than deterministic, the error
  magnitude is bounded by plausibility, and — unlike the budget verdict — the figure is not
  framed as verified, so it retains the ordinary status of a quoted price. Fully remediable: the
  correct values are in the log.
- **Related failures:** Downstream of *Provenance collapse through the summarization chain*,
  which is its cause. Distinct from *Fabricated budget verification*, where the invented inputs
  come from the pipeline rather than the model. Shares a detection mechanism with the budget mode
  — both reconcile output figures against the tool log — so one grounding check addresses both.
- **Variants:**
  - Optimizer asserts prices no tool returned *(brainstorm)*

## Intervention Points

### Prevention
- Ground the optimizer's figures in raw tool results rather than in summarized prose.

### Detection
- Reconcile every monetary figure in the itinerary against the raw tool log.

### Mitigation
- Regenerate ungrounded figures against the real prices, rather than removing them.

### Recovery
- Retain the tool log with the itinerary so ungrounded figures can be found after the fact.

---

## Management Plan

[Not yet developed. Run failure management to develop a plan for this failure mode.]
