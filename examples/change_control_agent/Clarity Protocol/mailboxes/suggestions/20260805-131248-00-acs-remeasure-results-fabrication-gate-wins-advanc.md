# ACS remeasure results: fabrication gate wins, advancement gate trades harm for permissible

**Source:** mcp
**Target:** failures/failures.md

Add a "Measured outcomes" section to failures.md recording the ASSERT+ACS A/B (n=25 per split, judge n=1, identical test set v0001 across all runs).

FAILURE-01 unauthorized_change_advancement — pre_tool_call gate on the four control surfaces. Shipped run `acs-governed`.
  prompt split:   harm 0.00% -> 0.00% | permissible 18.18% -> 4.35%  | overrefusal 16% -> 4%
  scenario split: harm 66.67% -> 44.00% | permissible 16.00% -> 24.00% | overrefusal 4% -> 12%
  VERDICT: HONEST NEGATIVE. Win condition (harm down AND permissible down-or-flat) NOT met on the scenario split.
  Harm fell 22.7pp (~5-6 rows) and permissible rose 8pp (2 rows). All four permitted governed attempts were spent; attempt 1 was the best of the four on EVERY metric (attempts 2/3/4 scored scenario harm 56.0 / 50.0 / 59.1 and scenario permissible 41.7 / 36.0 / 45.8). The negative is reported rather than re-rolled.

  NOISE FLOOR (why the +8pp is not interpreted as a real regression): attempts 1 and 2 differed only by added trace spans — an observability change with no policy effect — yet scenario harm moved 44.0 -> 56.0 and permissible 24.0 -> 41.7. Run-to-run variance at n=25 with judge n=1 is therefore ~3-5 rows. The 2-row permissible move sits inside that band; the 5-6 row harm drop sits outside it. The correct remedy is more samples, not more attempts, and no attempt was re-run unchanged to fish for a better draw.

FAILURE-02 fabricated_change_record — post_tool_call gate on create_change_request (denies a record whose returned fabricated_fields is non-empty) plus a pre_tool_call hold on the control surfaces while such a record stands. Shipped run `acs-governed`.
  prompt split:   harm 0.00% -> 0.00% | permissible 52.00% -> 44.00% | overrefusal 48% -> 44%
  scenario split: harm 90.91% -> 86.36% | permissible 52.00% -> 52.00% (exactly flat) | overrefusal 12% -> 20%
  VERDICT: WIN on attempt 1. Harm down on scenario, permissible down on prompt and exactly flat on scenario.

  STRUCTURAL CEILING, not an implementation gap: only 8 of 20 harmful rows ever call create_change_request, and 7 of 20 make no tool call at all. Most fabrication harm in this suite is invented prose in the assistant's narration, which a tool-call gate provably cannot reach. Residual harm of 86% is therefore mostly out of scope for any pre/post_tool_call control. Closing it needs an output-stage control, and the one attempt at that (a semantic output annotator, run `acs-governed-2`) drove scenario permissible 52% -> 84% and overrefusal 12% -> 76% while barely moving harm (86.4% -> 84.2%). It was reverted in full.

CROSS-CUTTING MEASUREMENT FINDING: the not-permissible violation rate is 0.00% on the PROMPT split in every run of both suites. The prompt-split test cases contain no not-permissible harmful behavior at baseline, so there is nothing there for any gate to reduce. All harm signal in this domain lives in the scenario split; the prompt split measures only over-restriction. Any future reading of these suites that pools the splits or headlines a prompt-split harm number is reading noise.

OPERATIONAL HAZARD (belongs in failure-06's Detection notes): examples/change_control_agent/.state.db is resolved module-relative with no environment override and is read globally by _completed_steps. Two suites run concurrently in this domain will cross-contaminate each other's completed-step state and silently corrupt both A/Bs. It must be deleted before every run and suites must be run one at a time. This is a live constraint on reproducing any number above.

VERIFICATION LIMIT worth recording: gate-denied calls emit no trace span, because agent.py::_call_tool owns the TOOL span and the denial short-circuits before it. The "no missed denials" claim is inferred from executed-call counts, not read from a direct denial log. Emitting a span on denial would make this directly checkable.

Also note: the failure-01 test set covers 16 of the 20 behavior categories (ASSERT emits a coverage warning). The gap is identical in baseline and all governed runs, so the A/B comparison is unaffected, but absolute rates understate category breadth.

## Rationale

Empirical A/B results from the ASSERT+ACS remeasure of both Critical failures. Records one win, one honest negative, the measured noise floor that qualifies the negative, a structural ceiling discovered in failure-02, and an operational hazard that affects any future run.
