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

The viewer top-level result moves from 8% impermissible violations at baseline
to 6% with the defensive prompt and 0% with ACS Rego. All three arms remain at
0% permissible violations.

### Behavior 2

On the historical viewer snapshot, Total 120 per arm:

| Arm | Impermissible | Permissible |
|---|---:|---:|
| Baseline | 8% | 27% |
| Hardened prompt | 0% | 47% |
| Classifier | 0% | 27% |

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

For the historical Behavior 2 comparison, the classifier preserved 20
percentage points more legitimate work than the hardened prompt while both
showed 0% impermissible violations. Do not use those rows as a release gate for
the current runtime until a new traced run records its model environment and
artifacts.

The release gate also runs the trust-boundary regressions: missing or invalid
tiers must stop writes before mutation; compound, wrong-payee, stale-action, and
otherwise mismatched references must escalate; missing learned annotations must
not allow; the tier and classifier arms must use native ACS with shim-policy
parity; and OTel evidence must include the current action binding plus
classifier deployment, calibration hash, and threshold version.

`fixtures/coercion_powered_120_arm_outcomes.json` contains one row per test case
and arm. The tests recompute the published counts and exact paired McNemar
result from that table instead of trusting the summary JSON alone. The raw
score and trace artifacts are not committed, so this repository does not claim
that it can independently verify trace lineage for those reviewed rows.

These thresholds are evidence for this agent and dataset, not universal release
defaults. Size and calibrate each gated suite for its own decision.
