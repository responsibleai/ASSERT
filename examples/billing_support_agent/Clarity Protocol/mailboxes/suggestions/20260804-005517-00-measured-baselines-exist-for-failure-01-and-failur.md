# Measured baselines exist for failure-01 and failure-02

**Source:** mcp
**Target:** failures/failures.md

failure-01 (unverified high-risk action) and failure-02 (cross-customer data exposure / BOLA) each have a measured ASSERT baseline. Configs: examples/billing_support_agent/evals/unverified-high-risk-action/eval_config.yaml and .../cross-customer-data-exposure/eval_config.yaml. Baseline harm (non-permissible policy violation, prompt/scenario): failure-01 = 4% / 8.7%; failure-02 = 20.8% / 43.75%. Overrefusal near zero in both. Next step: govern with an ACS structural gate (pre-tool-call verification for failure-01; account-scoping arg gate for failure-02) and re-run to prove the delta.

## Rationale

A measured ASSERT baseline now exists for both Critical P1 risks, so Clarity's failure records should point to where the eval and evidence live and note the current harm rates.
