# ASSERT evals as an AI-safety regression gate in CI

The control-plane arm of this demo also ships as a **CI gate**, in its own standalone
repository — because that is how teams actually adopt it: your agent is its own project that
installs ASSERT as a dependency and wires it into `.github/workflows/`, not a fork of ASSERT.

**→ [`responsibleai/assert-ci-banking-demo`](https://github.com/responsibleai/assert-ci-banking-demo)**
&nbsp;*(the canonical CI shipping vehicle)*

There, the banking agent simply does:

```bash
pip install "assert-ai[acs,langgraph,otel,examples]"
```

and adds an ASSERT safety-regression gate to CI. The gate replays a committed ASSERT run,
compares it to the recorded baseline arm with a paired statistical test, and **passes only if
the change significantly *improves* the safety dimension without regressing `overrefusal`** —
an improvement gate, not a fixed threshold. If it fails, the build is skipped and the PR is
blocked.

## What the gate is testing for, in this example's terms

Both behaviors in this example produce exactly the shape of evidence the gate consumes — a
frozen test set, several arms scored against it, and two axes that must move in the right
direction *together*:

| Behavior | Config to gate on | Safety axis | Quality axis |
|---|---|---|---|
| 1 · sensitivity-tier authorization | [`eval_tier_authorization_traced.yaml`](../eval_tier_authorization_traced.yaml) | `policy_violation` | `overrefusal` |
| 2 · coercion via unverified authority | [`eval_coercion_arm3_acs.yaml`](../eval_coercion_arm3_acs.yaml) | `coercion_bypass` | `legitimate_escalation_overrefusal` |

Use the **traced** config for Behavior 1: with a text-only target the judge cannot establish
the ordering property the behavior is about, and the dimension saturates across arms —
a gate built on it would pass everything.

Both behaviors also show why a *single-axis* gate is a trap:

- Behavior 1's defensive-prompt arm leaves unauthorized exposure on the domains the baseline
  never covered at **exactly** its baseline rate — a prompt-only change that looks harmless
  and fixes nothing.
- Behavior 2's hardened-prompt arm closes the bypass but drives over-refusal from 42.9% to
  71.4%. A safety-only gate would have scored that as a clean win.

A two-axis improvement gate rejects both.

> **Statistical note.** Behavior 2's arms are n=19 coercive / n=21 legitimate; none of the
> bypass deltas reach p<0.05 (all p=0.31), and the closest contrast is the hardened prompt's
> over-refusal regression at p=0.061. A paired improvement gate at that sample size will not
> reach significance — size the gated suite for the decision you want it to make.

## Why a standalone repo is the right shipping vehicle

- **Matches how teams work** — the agent is a normal project that `pip install`s `assert-ai`;
  nobody develops inside a cloned eval framework.
- **CI is the natural home for the control-plane arm** — a gate belongs in the target repo's
  `.github/workflows/`, next to the code it guards.
- **Deterministic** — the standalone repo commits pre-computed ASSERT artifacts, so every CI
  run is reproducible with no live LLM calls.

This directory is the in-repo reference and pointer; the standalone repo is where you copy the
pattern into your own agent project.

