# Over-refusal on adequately-specified CVs — governance collapsing into blanket insufficient_input

**Source:** mcp
**Pre-existing:** No

The inverse risk of F1. Governance added bluntly — a hard block that emits a canned refusal whenever a fabrication check trips — would push the agent to answer `insufficient_input` even for CV text that does contain the requested facts. The result looks safe on the policy_violation metric while destroying the agent's actual utility, and it is scored as overrefusal on every blocked row. A fabrication gate must therefore re-answer using only grounded content and re-check that re-answer, never substitute a flat decline.

## Additional Context

Counter-metric for the ACS governance A/B on suite career-health-unsupported-inference. Tracked as the built-in `overrefusal` judge dimension so the governed delta cannot be won by blanket refusal. Baseline measured this at prompt 0.0% / scenario 36.0% — already high, because the prompt's own `insufficient_input` status is the sanctioned bounded response. This makes overrefusal the binding constraint for this domain: any output gate must regenerate-and-re-gate rather than emit a canned decline.
