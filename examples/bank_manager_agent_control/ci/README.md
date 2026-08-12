# Use ASSERT results as an SDLC regression gate

The bank support agent produces the inputs a release gate needs:

- one behavior per config;
- one frozen test set across candidate arms;
- impermissible and permissible behavior measurements;
- full evidence for every failed case; and
- paired comparisons where the same cases are rerun.

The standalone shipping example remains:

[`responsibleai/assert-ci-banking-demo`](https://github.com/responsibleai/assert-ci-banking-demo)

## Gate both safety and product quality

| Behavior | Impermissible dimension | Permissible dimension |
|---|---|---|
| Sensitivity-tier authorization | unauthorized exposure / `policy_violation` | standard-tier request mishandled / `overrefusal` |
| Coercion via unverified authority | `coercion_bypass` | `legitimate_escalation_overrefusal` |

The display-level product language is **Impermissible behavior violated** and
**Permissible behavior violated**. The lower-level dimension keys above remain
useful for behavior-specific automation.

### Behavior 1

The defensive prompt does not move the uncovered domains. The Rego arm reduces
impermissible exposure to zero in the measured runs while preserving standard
requests.

### Behavior 2

On the reviewed 120-case dataset:

| Arm | Impermissible | Permissible |
|---|---:|---:|
| Baseline | 8.3% | 26.7% |
| Hardened prompt | 0.0% | 46.7% |
| Classifier | 0.0% | 26.7% |

A safety-only gate would accept the hardened prompt. A two-axis gate rejects it
because it creates 20.0 percentage points more permissible violations.

## Recommended CI logic

1. Install the reviewed fixture.
2. Execute the baseline and candidate on the same case IDs.
3. Reject any regression in the impermissible or permissible dimensions.
4. Use a paired test for the predeclared primary comparison.
5. Attach the failed cases and trace evidence to the build summary.
6. Include model/tool cost and latency when the release decision is
   cost-sensitive.

For the published Behavior 2 comparison, the classifier-vs-hardened permissible
delta is -20.0 points with exact paired McNemar `p=.0169`. The classifier
observed 0/60 coercion bypasses; its one-sided exact upper 95% bound is 4.87%.

These thresholds are evidence for this agent and dataset, not universal release
defaults. Size and calibrate each gated suite for its own decision.
