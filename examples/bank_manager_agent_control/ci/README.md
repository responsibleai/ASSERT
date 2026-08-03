# Beat 3 in CI — ASSERT evals as an AI-safety regression gate

The third beat of the demo — the control plane, enforced in **CI** — ships as its own
standalone repository, because that is how teams actually adopt it: your agent is its own
project that installs ASSERT as a dependency and wires it into `.github/workflows/`, not a
fork of ASSERT.

**→ [`responsibleai/assert-ci-banking-demo`](https://github.com/responsibleai/assert-ci-banking-demo)**
&nbsp;*(the canonical CI shipping vehicle)*

There, the banking agent simply does:

```bash
pip install "assert-ai[acs,langgraph,otel,examples]"
```

and adds an ASSERT safety-regression gate to CI. The gate replays a committed ASSERT run,
compares it to the unguarded production baseline with a paired statistical test, and
**passes only if the change significantly *improves* `policy_violation` without regressing
`overrefusal`** — an improvement gate, not a fixed threshold. If it fails, the build is
skipped and the PR is blocked.

Two demo pull requests make the beat concrete, each measured against the same unguarded
baseline (`policy_violation` 54%, `overrefusal` 19%):

| PR | Change | `policy_violation` vs baseline | Gate |
|----|--------|-------------------------------|------|
| Defensive **system-prompt** | prompt-only hardening | 54% → 62% (no significant improvement) | ❌ **FAIL** |
| **Control plane** (ASSERT + ACS) | typed-feature gate | 54% → 17% (improved; over-refusal 19% → 8%) | ✅ **PASS** |

The story mirrors the live demo: **prompting alone doesn't clear the safety bar; the
structural control plane does — and CI enforces it.** See the standalone repo for the
workflow, the gate script, and the real pass/fail PR action-run summaries.

## Why a standalone repo is the right shipping vehicle

- **Matches how teams work** — the agent is a normal project that `pip install`s
  `assert-ai`; nobody develops inside a cloned eval framework.
- **CI is the natural home for the control-plane beat** — a gate belongs in the target
  repo's `.github/workflows/`, next to the code it guards.
- **Deterministic** — the standalone repo commits pre-computed ASSERT artifacts, so every
  CI run is reproducible with no live LLM calls.

This `examples/ci/` directory is the in-repo reference and pointer; the standalone repo is
where you copy the pattern into your own agent project.
