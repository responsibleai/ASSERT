# Leakage gate measured: harm roughly halved

**Source:** mcp
**Target:** failures/failures.md

Annotate failure-01 (Confidential/internal leakage) as MEASURED and GOVERNED. Baseline harm (non-permissible policy-violation): prompt 40.9%, scenario 62.5%. With the committed ACS output-annotator gate (examples/azure_doc_qa/acs/confidential-internal-leakage) enforced via agent_guarded.py:chat_governed_leakage: prompt 9.1%, scenario 33.3% — harm cut by ~31.8 pts (prompt) and ~29.2 pts (scenario), at an overrefusal cost of +8 pts (prompt) and +20 pts (scenario). Eval configs: examples/azure_doc_qa/evals/confidential-internal-leakage/{eval_config.yaml, eval_config.governed.yaml}. Verdict: reply-only content-classification gate is effective for this risk; net win.

## Rationale

Failure-01 now has measured baseline and governed deltas, so the failures doc should reflect it is validated with a working mitigation rather than an untested candidate.
