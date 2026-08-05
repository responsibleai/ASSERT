# Budget overrun: measured baseline harm already below governance threshold; do not gate

**Source:** mcp
**Target:** failures/failures.md

Mark failure-02 (budget_overrun) as MEASURED-BASELINE, NOT GOVERNED. A/B measurement at n=25/type showed non-permissible HARM of only 0pct/4.5pct (prompt/scenario), below the threshold where a blocking control is warranted. The agent's real weakness on budget is over-refusal (14 cases; it deflects instead of confirming an in-budget total it already holds), which a gate would only worsen. Decision (user-confirmed): leave budget baseline-only. Follow-up if ever needed: address the over-refusal, not harm, via prompt guidance rather than a blocking gate.

## Rationale

Records the evidence-based decision to skip governance for a risk whose measured harm is already controlled, and flags the real (over-refusal) weakness for future work.
