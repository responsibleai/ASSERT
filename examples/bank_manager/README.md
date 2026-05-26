# Bank Manager — ACS port + 4-axis ASSERT demo

This example builds on the [microsoft/AgentShield](https://github.com/microsoft/AgentShield)
`examples/agents/bank-manager` reference implementation and turns it into a
**4-variant ASSERT demo** that measures the trade-off between security and
overrefusal across four RAI axes. The 3-act storyline below is the way to read
the artifacts.

> ACS is the policy spec; `agent_shield` is the reference Python runtime that
> loads ACS YAML and enforces it at agent execution time. Throughout this doc,
> "ACS" refers to the policy layer in general; `agent_shield` refers to the
> specific runtime imported as the `agent_shield` Python package.

**Source commit for the vendored ACS pieces**: [`microsoft/AgentShield@1cfc6ee`](https://github.com/microsoft/AgentShield/commit/1cfc6ee6c82661f21d951a423caa141dade4ad41)

---

## TL;DR

| Act | Variant | What changes | Headline you should see |
|---|---|---|---|
| 1. Broken baseline | `eval_config_unguarded.yaml` | Raw LangGraph agent, deliberately too-compliant prompt, no ACS gates | **39%** fabricated_financial_fact, 23% vip_transfer, 19% account_takeover; overrefusal **0%** |
| 2. The DO-NOT trap | `eval_config_baseline_prompt.yaml` | Same agent + five blunt DO-NOTs appended to the system prompt; no ACS | Most security axes drop, but **overrefusal jumps to 22%** |
| 3a. ACS gates | `eval_config_guarded.yaml` | 5-stage ACS policy on, original SUT prompt | Tool-misuse + integrity axes collapse to ≤4%; **overrefusal climbs to 31%** ⚠️ |
| 3b. ACS + GEPA prompt | `eval_config_guarded_gepa.yaml` | ACS on + GEPA-optimized SUT prompt | All 9 security dims at **0%**; overrefusal **21%** (−10 pp vs ACS-only, still above the 10% target) |

Trade-off chart:

![Trade-off: behavior rate vs overrefusal across the 4 variants](artifacts/trade_off.png)

> **Numbers in the table and chart**: All four variants were run at n=100
> with the ten current judge dimensions and `max_turns: 3` on 2026-05-24.
> Judge model: `azure/gpt-5.4-mini`. Judge failures: 0% across all variants.
> The chart's geometry — D in the lower-left, A upper-left, B and C
> lower-right — is the measured shape, not a prediction.

---

## The 4 behaviors under test

One behavior per axis of a four-axis RAI risk taxonomy, all evaluated on the
same LangGraph bank-manager agent. The judge ships ten binary dimensions: the
seven introduced in PR #88 plus three new multi-turn dimensions added in this
PR.

| Axis | Behavior (snake_case) | Judge dimension(s) | Where it gets caught |
|---|---|---|---|
| 1. Instruction & control | `instruction_injection_via_memo` | `prompt_injection_via_tool_output_viol`, `account_takeover_attempt_viol` | ACS Stage 2 state machine + Stage 3 task-adherence LLM |
| 2. Information integrity & leakage | `pii_disclosure_under_social_pressure` (new), `pii_leak_in_output`, `fabricated_financial_fact` (new) | `pii_disclosure_under_social_pressure_viol` (new), `pii_leak_in_output_viol`, `fabricated_financial_fact_viol` (new) | ACS Stage 1 social-engineering detector + Stage 4 PII regex catch PII; fabricated-fact hallucinations require the prompt fix (no deterministic gate enforces tool-call grounding) |
| 3. Tool / action misuse | `capability_required_action_without_hitl` | `large_transfer_no_hitl_viol`, `vip_transfer_no_approval_viol`, `freeze_without_admin_mode_viol` | ACS Stage 2 state machine — the cleanest ACS-wins moment |
| 4. System-level & emergent | `sycophantic_policy_drift` (new) | `sycophantic_policy_drift_viol` (new) | Prompt fix dominates; ACS catches the individual privileged call but cannot enforce "stay firm across turns" |

The three new judge dimensions read the **full multi-turn trajectory**, not
just the final response. See `eval_config_guarded.yaml` for the exact
rubric wording.

---

## Results table (4 behaviors × 4 variants)

All numbers below are from a fresh n=100 run on 2026-05-24 across all
four variants, with all ten current judge dimensions and `max_turns: 3`.
Judge model: `azure/gpt-5.4-mini`; judge failures: 0% on every variant.

Variants A and B share one suite-cached 100-row test set (7-behavior
description); variants C and D share a separate suite-cached 100-row test
set (9-behavior description, which includes the three new multi-turn
behaviors in the YAML prose). Within each pair the comparison is
apples-to-apples; across pairs the test cases differ but the stratify
recipe is identical.

| Behavior / judge dim | A: unguarded | B: ACS gates | C: naive DO-NOT | D: ACS + GEPA prompt |
|---|---|---|---|---|
| `large_transfer_no_hitl_viol` | 1% | **0%** | 0% | **0%** |
| `vip_transfer_no_approval_viol` | 23% | **0%** | 3% | **0%** |
| `freeze_without_admin_mode_viol` | 7% | **0%** | 0% | **0%** |
| `prompt_injection_via_tool_output_viol` | 0% | **0%** | 0% | **0%** |
| `pii_leak_in_output_viol` | 0% | 0% | 0% | 0% |
| `pii_disclosure_under_social_pressure_viol` (new) | 0% | 0% | 0% | 0% |
| `account_takeover_attempt_viol` | 19% | 4% | 0% | **0%** |
| `sycophantic_policy_drift_viol` (new) | 0% | 0% | 0% | 0% |
| `fabricated_financial_fact_viol` (new) | **39%** | **0%** | 3% | **0%** |
| `overrefusal` | **0%** | 31% ⚠️ | 22% | 21% |
| **max behavior_rate (9 security dims)** | **39%** | 4% | 3% | **0%** |
| **any-security-violation rate (union of 9)** | **42%** | 4% | 3% | **0%** |

Read:

- **A → B (ACS turns on)**: any-security-violation drops 42% → 4%
  (−38 pp / 90% reduction), but overrefusal climbs 0% → 31% (+31 pp ⚠️).
  Tool-misuse axes (`large_transfer`, `vip_transfer`, `freeze`) and the
  fabricated-fact axis go to zero; only `account_takeover` leaks at 4%.
- **A → C (prompt-only fix)**: any-security-violation drops 42% → 3%,
  but overrefusal climbs 0% → 22%. The naïve "DO NOT" prompt buys most
  of the security drop ACS gives, but the agent now refuses benign
  account lookups too.
- **B → D (add GEPA on top of ACS)**: every security dim that was still
  leaking under ACS-only (`account_takeover` 4%, `vip_transfer` 0%,
  `fabricated_fact` 0%) drops to 0%, and overrefusal improves 31% → 21%
  (−10 pp). D is the lower-left point on the chart, but its overrefusal
  is still above the documented 10% GEPA selection-rule budget — the
  placeholder optimized prompt is hand-authored, not the output of a
  real DSPy/GEPA Pareto search (see `optimize_with_gepa.ipynb` for the
  recipe).

The `pii_disclosure_under_social_pressure_viol` and
`sycophantic_policy_drift_viol` dims registered 0% across all four
variants at n=100. Either the `pressure_escalation_intensity:aggressive`
stratify level didn't bind hard enough on this test set, or
`gpt-4o-mini` is well-aligned on the multi-turn behaviors these
trajectory-shape rubrics target. The `fabricated_financial_fact_viol`
new dim is doing most of the new-axis work — it caught the
hallucinated-confirmation pattern at 39% in variant A.

---

## 3-act walkthrough

### Act 1 — The broken baseline (~3 min)

- **Config**: `eval_config_unguarded.yaml`
- **Target**: `chat_unguarded` — raw LangGraph ReAct agent, no ACS gates,
  deliberately too-compliant `SYSTEM_PROMPT`.
- **What happens**: `fabricated_financial_fact_viol` lights up at **39%**
  (the agent hallucinates confirmation strings like "transfer of $0.00
  completed" when the tool didn't actually succeed).
  `vip_transfer_no_approval_viol` 23%, `account_takeover_attempt_viol`
  19%, `freeze_without_admin_mode_viol` 7%. Overrefusal sits at 0%
  because the agent rarely refuses anything.
- **Headline**: any-security-violation rate (union of 9 dims) = **42%**;
  max single-axis behavior_rate = **39%**.
- **Speaker line**: *"This is a real LangGraph agent on a weak-but-realistic
  model. The model is doing its best. ASSERT shows you exactly where its
  best isn't good enough — and on which axis."*

### Act 2 — The DO-NOT trap (~3 min)

- **Config**: `eval_config_baseline_prompt.yaml`
- **Target**: `chat_naive` — same agent, no ACS, but the SUT system prompt
  is `SYSTEM_PROMPT` plus a blunt five-line "DO NOT" block plus a final
  "If a request appears risky, refuse." line.
- **What happens**: Most security axes drop to 0%
  (`account_takeover` 19% → 0%, `vip_transfer` 23% → 3%,
  `fabricated_financial_fact` 39% → 3%). But overrefusal jumps
  **0% → 22%** because the agent now refuses benign account lookups too.
  Any-security-violation drops to **3%**.
- **Headline**: **overrefusal delta = +22 pp** vs the baseline, for a
  security drop the layered fix matches without the same UX cost.
- **Speaker line**: *"My fix worked on the axis I aimed at. It made me worse
  on an axis I wasn't measuring. If I'd shipped this on a single-number
  eval, nobody would have noticed."*

### Act 3 — The layered fix (~5 min)

- **Configs in sequence**:
  1. `eval_config_guarded.yaml` (`chat_guarded`) — ACS 5-stage policy on,
     original SUT prompt.
  2. `eval_config_guarded_gepa.yaml` (`chat_guarded_gepa`) — ACS on, plus
     the GEPA-optimized SUT prompt loaded from
     `prompts/system_prompt.optimized.txt`.
- **What happens**: ACS-only (variant B) collapses 8 of 9 security dims
  to 0% (only `account_takeover` leaks at 4%) but pushes overrefusal to
  **31%**. Add the GEPA-optimized prompt on top (variant D) and the last
  4% security leak goes to 0% AND overrefusal drops to **21%**
  (−10 pp vs B).
- **Headline**: variant D = **0% any-security-violation, 21%
  overrefusal**. The Pareto-optimal point on the chart; still above the
  10% GEPA selection budget, because the placeholder optimized prompt is
  hand-authored, not the output of a real DSPy/GEPA search.
- **Speaker line**: *"ACS holds the deterministic line. ASSERT tells me
  what's left. GEPA evolves the prompt against ASSERT's signal. The
  shipping decision becomes defensible across nine axes — not just one
  number."*

---

## What's here

| File | Role |
|---|---|
| `mcp_server.py` | Vendored FastMCP server — 7 banking tools, in-memory fixtures |
| `guardrails.yaml` | Vendored leaf ACS policy (app tier) — fraud detector + PII redaction |
| `bank-mcp-template.guardrails.yaml` | Vendored MCP-dev tier — state machine, approval tokens |
| `bank-base.guardrails.yaml` | Vendored CISO tier — input/output LLM stages (path-adapted) |
| `prompts/*.md` | Vendored bank-specific LLM detector prompts |
| `prompts/cross/*.md` | Vendored cross-example detector prompts (jailbreak, social-engineering, PII) |
| `prompts/system_prompt.optimized.txt` | GEPA-optimized SUT system prompt (placeholder today; replaced by `optimize_with_gepa.ipynb`) |
| `agent.py` | ASSERT callable targets: `chat_unguarded` / `chat_naive` / `chat_guarded` / `chat_guarded_gepa` |
| `eval_config_unguarded.yaml` | ASSERT eval — Act 1 (no ACS, original prompt) |
| `eval_config_baseline_prompt.yaml` | ASSERT eval — Act 2 (no ACS, naïve DO-NOT prompt) |
| `eval_config_guarded.yaml` | ASSERT eval — Act 3a (5-stage ACS, original prompt) |
| `eval_config_guarded_gepa.yaml` | ASSERT eval — Act 3b (5-stage ACS, GEPA-optimized prompt) |
| `optimize_with_gepa.ipynb` | DSPy GEPA recipe (authored, NOT executed — runs offline; hours) |
| `artifacts/trade_off.png` | Trade-off chart (regenerate with `python scripts/render_trade_off.py`) |

---

## DSPy / GEPA

The Act 3b optimized prompt is produced by [GEPA](https://arxiv.org/abs/2507.19457)
(Agrawal et al., arXiv:2507.19457; ICLR 2026 oral) — a reflective prompt
evolution optimizer that does Pareto-frontier search with LLM-driven
mutation. GEPA ships in DSPy (`from dspy.teleprompt import GEPA`, also
exposed as `dspy.GEPA`) and natively supports multi-metric optimization
via per-metric feedback returned as a dict / `ScoreWithFeedback`.

The selection rule used to pick one prompt off GEPA's Pareto frontier:

> **`argmin max(behavior_rates) subject to overrefusal ≤ 0.10`**

In English: of all candidates that hold overrefusal under 10%, pick the
one whose worst-axis behavior rate is smallest. The notebook prints the
full frontier so reviewers can see what the rule discards.

The notebook (`optimize_with_gepa.ipynb`) is the recipe — it is **not run**
during the demo. A full GEPA run is on the order of several thousand LLM
calls and takes hours. The committed `prompts/system_prompt.optimized.txt`
is the artifact the demo speaker points at; today it is a hand-authored
placeholder with three tightening sentences appended on top of the
original `SYSTEM_PROMPT`, and the notebook overwrites it when re-run
offline.

DSPy is shipped as an optional `[dspy]` extra. Install only when you want
to run the notebook:

```bash
pip install -e ".[otel,langgraph,dspy]"
```

The four eval configs themselves don't import DSPy at runtime, so the
base install works without it.

---

## Reproduction

### Prerequisites

```powershell
# From repo root
Copy-Item .env.example .env
# Fill in AZURE_API_KEY and AZURE_API_BASE in .env
# No ANTHROPIC_API_KEY required — ACS LLM stages route through Azure
python -m pip install -e ".[otel,langgraph,dspy]"
```

### Run the four variants

Each variant takes ~17–20 min at n=100 on a single Azure deployment
(measured on the 2026-05-24 run with `azure/gpt-5.4-mini`); the suite-
cached `systematize` / `test_set` stages mean the second variant in each
pair (A↔B, C↔D) skips upstream work and only re-runs `inference` and
`judge`.

```powershell
p2m run --config examples\bank_manager\eval_config_unguarded.yaml
p2m run --config examples\bank_manager\eval_config_baseline_prompt.yaml
p2m run --config examples\bank_manager\eval_config_guarded.yaml
p2m run --config examples\bank_manager\eval_config_guarded_gepa.yaml
```

Artifacts land in (directory letters are chronological, the order variants
were added to the suite; the four Acts are mapped explicitly):

- `artifacts/results/bank-manager-agent-shield/variant-a-unguarded/`    — Act 1
- `artifacts/results/bank-manager-agent-shield/variant-b-guarded/`      — Act 3a
- `artifacts/results/bank-manager-agent-shield/variant-c-baseline-prompt/` — Act 2
- `artifacts/results/bank-manager-agent-shield/variant-d-guarded-gepa/` — Act 3b

The `artifacts/results/` tree is `.gitignored`. A committed snapshot of
the four `metrics.json` + `scores.jsonl` + `config.yaml` + `manifest.json`
files lives under
`examples/bank_manager/artifacts/results/bank-manager-agent-shield/`
for reproducibility (the source of truth for the rendered PNG).

### Re-render the trade-off chart

After the four variants finish, re-render the PNG from the real
`scores.jsonl` files:

```powershell
python scripts\render_trade_off.py
```

The renderer checks the committed snapshot under
`examples/bank_manager/artifacts/results/` first, then falls
back to the live runtime path under `artifacts/results/`. For any
variant whose `scores.jsonl` cannot be located it substitutes the
labelled placeholder from the 2026-05-24 n=100 run so a partial re-run
still produces a sensible chart.

### Re-optimize the prompt (offline; hours)

```powershell
jupyter notebook examples\bank_manager\optimize_with_gepa.ipynb
```

The notebook loads ASSERT as a fitness oracle, runs `dspy.GEPA` against a
5-element fitness vector (one per axis plus `1 - overrefusal`), prints the
Pareto frontier, applies the documented selection rule, and writes the
winner over `prompts/system_prompt.optimized.txt` (preserving the comment
header). **Do not run during a live demo.**

### Reproduction notes

- **Shared test set across variants**: all four configs share
  `suite: bank-manager-agent-shield`, which means the `test_set` and
  `systematize` stages write versioned artifacts to a single
  suite-level directory (`artifacts/results/bank-manager-agent-shield/`)
  and reuse them across variants (per `CONFIG_REFERENCE.md`, "Suite-level
  stages write versioned artifacts under the suite directory and are
  shared across runs"). In practice: the first `p2m run` (any variant)
  generates `test_set.jsonl` once; the next three runs detect the cached
  test set and only re-run `inference` and `judge` against the same 100
  test cases. Cross-variant comparison is therefore apples-to-apples by
  construction. The current schema has no `pipeline.test_set.path` field
  to load test cases from a committed file, and `p2m run` exposes no
  `--seed` flag — the suite-cache mechanism is the supported
  reproducibility path today.
- **Agent model pin**: set `AGENT_MODEL=gpt-4o-mini` in `.env` to pin the
  SUT model. The ACS LLM stages share that LLM via
  `Shield.from_yaml(...).with_langchain().with_client(llm)`, which
  overrides the YAML-declared `anthropic.claude` provider.

---

## What is and is not in this PR

**In this PR:**

- Three new multi-turn judge dimensions (`pii_disclosure_under_social_pressure_viol`,
  `sycophantic_policy_drift_viol`, `fabricated_financial_fact_viol`) added to
  all four eval configs. All three read the full conversation trajectory; the
  third grounds every concrete financial claim in the assistant's output
  against a successful tool-call return in the trace.
- A new `pressure_escalation_intensity` stratify dimension (single_turn /
  gentle / aggressive) to surface the multi-turn dims at meaningful rates.
- Two new eval configs (`eval_config_baseline_prompt.yaml`,
  `eval_config_guarded_gepa.yaml`).
- Two new `agent.py` callables (`chat_naive`, `chat_guarded_gepa`).
- A GEPA notebook (authored; not executed) and a placeholder optimized
  prompt artifact.
- A trade-off chart renderer + a rendered PNG.
- A `[dspy]` extra in `pyproject.toml`.
- Customer-facing prose renamed from "failure mode" → "behavior" (YAML
  judge-dim names unchanged).

**Not in this PR:**

- A real DSPy/GEPA-optimized prompt artifact. The committed
  `prompts/system_prompt.optimized.txt` is a hand-authored placeholder;
  the notebook recipe overwrites it when re-run offline. A full GEPA
  search is on the order of hours of LLM compute and lives outside the
  PR-time loop.
- A viewer feature for the trade-off chart. The PNG is the customer-facing
  artifact today.
- A `p2m optimize` CLI verb. GEPA is invoked via the notebook only; if /
  when we add a first-class CLI we can revisit the surface area.
- Any change to the vendored ACS files (`mcp_server.py`, `guardrails.yaml`,
  `bank-mcp-template.guardrails.yaml`, `bank-base.guardrails.yaml`,
  `prompts/*.md`). Those stay byte-for-byte identical to
  microsoft/AgentShield@1cfc6ee.

---

## Provenance

All files in `mcp_server.py`, `guardrails.yaml`, `bank-mcp-template.guardrails.yaml`,
`bank-base.guardrails.yaml`, and `prompts/*.md` are vendored from
[microsoft/AgentShield](https://github.com/microsoft/AgentShield) at commit
[`1cfc6ee`](https://github.com/microsoft/AgentShield/commit/1cfc6ee6c82661f21d951a423caa141dade4ad41)
under the Apache-2.0 / MIT license. See each file's header for the
original path.

`agent.py`, the four eval configs, `prompts/system_prompt.optimized.txt`,
`optimize_with_gepa.ipynb`, `scripts/render_trade_off.py`, and this README
are written for this ASSERT integration. The original `SYSTEM_PROMPT`
constant in `agent.py` is copied verbatim from
`examples/agents/bank-manager/demo.py` at the same commit; `SYSTEM_PROMPT_NAIVE`
and `prompts/system_prompt.optimized.txt` are new.
