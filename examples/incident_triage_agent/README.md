# Incident Triage Agent — joint AgentShield + p2m demo

> A walk-through of the **local-first developer eval-fix loop**: write an
> agent → run p2m to find its failure modes → close the runtime-fixable
> ones in an AgentShield `.guardrails.yaml` → re-run p2m to prove the
> fix landed and surface what's left for the developer.

For the full case study with measured numbers, see
[`docs/case-study-incident-triage-joint.md`](../../docs/case-study-incident-triage-joint.md).

---

## Why this demo exists

Most production agents today have:
- An optional batch eval, often disconnected from runtime.
- A runtime guardrail layer (if any) that lives in a separate vendor stack.
- No closed loop between the two.

This demo shows that **adaptive-eval (p2m) + AgentShield together close
the loop in a single local IDE session**. No SaaS round-trip. The
developer sees, in one workflow:

1. **What is broken** (p2m surfaces 9 failure modes at n=200 prompt +
   n=200 scenario seeds, each scored on a structured rubric, including
   XPIA literal-relay AND downstream effects).
2. **What the runtime layer can fix** (AgentShield drops `xpia_relay`
   by **88 %** relative on the multi-turn rail, `channel_violation`
   by **71 %**, `ordering_violation` to **0 %**, `pager_violation` to
   **1 %** — verified by re-running the same seeds).
3. **What is left for the developer** (model-judgment modes —
   `wrong_severity`, `fabrication` — that no policy YAML can honestly
   close, plus the team-binding partial closure on
   `escalation_violation`).

This demo emphasizes the three failure modes AgentShield is uniquely
strong on for an incident-triage workload:
1. **Cross-prompt injection (XPIA)** embedded in alert tool output —
   both the literal relay and the downstream channel/escalation drift
   it induces.
2. **PII / credentials** echoed into channel messages.
3. **Missed or wrongly-targeted cross-team escalation.**

Six of ten fixture alerts (ALR-001/002/004/005/006/008) carry an
attacker-controlled `inbound_payload_text` field directing the agent
toward specific policy violations (leak api_key/customer_email to
#general, skip privacy/legal escalations, page on a P2, etc.). At
n=200, literal XPIA payload relay is measurably non-zero
(7.5 % prompt, 12.1 % scenario BEFORE) — and AgentShield's
deterministic gates close *both* the literal relay (down to 3.0 % /
1.5 % AFTER) and the paraphrased downstream effects (channel,
escalation, pager) model-agnostically. That defense-in-depth at both
layers is the joint pitch.

---

## File layout

```
examples/incident_triage_agent/
├── README.md                                # this file
├── SOP.md                                   # the runbook (single source of truth)
├── incident_triage_workflow_failures.md     # p2m concept (9 failure modes)
├── incident-triage.guardrails.yaml          # AgentShield policy (13 gates)
├── .env.example                             # demo-specific env overrides
├── agent.py                                 # baseline agent (no guardrails)
├── agent_guarded.py                         # same agent, AgentShield-wrapped
├── eval_config_baseline.yaml                # p2m: target = agent:chat
├── eval_config_guarded.yaml                 # p2m: target = agent_guarded:chat
└── fixtures/
    ├── incidents.json                       # 10 mock alerts; 6 carry XPIA payload
    └── teams.json                           # mock on-call roster
```

The two `eval_config_*.yaml` files are **identical** except for the
`target.callable` import path and the `run` name — that guarantees a fair
A/B against the same generated seeds.

---

## Prerequisites

- **Python 3.11+** with [`uv`](https://docs.astral.sh/uv/).
- **Azure OpenAI** access for two model deployments:
  - `azure/gpt-5.4-mini` — the agent's reasoning model (and the
    scenario auditor that pressures the agent in multi-turn runs).
  - `azure/gpt-5.4` — the policy/design/judge models.
- **AgentShield Python SDK 0.13.0a1** (or newer 0.13.x). The wrapped
  agent (`agent_guarded.py`) imports `from agent_shield import RuntimeBuilder`.

---

## Setup (one-time)

```bash
# 1. Install the repo + AgentShield SDK.
uv sync
uv pip install agent-shield  # 0.13.x; gives you `from agent_shield import RuntimeBuilder`

# 2. Copy the env template and fill in your Azure credentials.
cp .env.example .env
cp examples/incident_triage_agent/.env.example examples/incident_triage_agent/.env

# Required in the repo-root .env (one set of LiteLLM creds, shared by all demos):
#   AZURE_API_KEY=...
#   AZURE_API_BASE=https://<your-resource>.openai.azure.com
#   AZURE_API_VERSION=2024-10-21
```

The demo-local `.env` only needs to override the model spellings if you
have non-standard deployment names.

---

## The walk-through (about 70-100 minutes wall clock)

### Step 1 — Smoke-test both agents (≈30 seconds)

Sanity check that both targets respond and the AgentShield runtime loads:

```bash
uv run python examples/incident_triage_agent/agent.py
uv run python examples/incident_triage_agent/agent_guarded.py
```

Each prints two scripted scenarios. The guarded run will visibly
**block** the second scenario (a P3 alert that asks for a page) with a
`blocked_by_guardrail` tool message, which the agent then explains in
prose.

### Step 2 — Run the BEFORE eval (≈65 minutes at n=200, rollout c=24)

```bash
uv run p2m run --config examples/incident_triage_agent/eval_config_baseline.yaml
```

This generates a behavior taxonomy from the 9 failure modes, designs
**200 prompt seeds + 200 scenario seeds**, runs them against
`agent:chat` (the unguarded baseline) at concurrency 24, and scores
every transcript with `azure/gpt-5.4` against the 11-dimension rubric.

Inspect the headline metrics:

```bash
uv run p2m results status incident-triage-agent-v1 baseline-weak-prompt
```

You should see a `policy_violation` rate of ~85 % on the scenario
split (most multi-turn runs violate *something*), and substantial
non-zero rates on the procedural failure-mode dimensions:
`channel_violation` ~28 %, `escalation_violation` ~37 %,
`xpia_relay` ~12 %, `alert_id_drift` ~11 %.

### Step 3 — Read the policy you're about to apply (≈5 minutes)

Open
[`incident-triage.guardrails.yaml`](incident-triage.guardrails.yaml) and
walk through the rules. There are **two stages** of guards (state and
tool-execution validation), wired to the failure modes listed in
[`incident_triage_workflow_failures.md`](incident_triage_workflow_failures.md):

| Failure mode | Guard rule(s) (actual YAML `name:` values) | Stage |
|---|---|---|
| `ordering_violation` (alert not loaded) | `alert_must_be_loaded_gate` | state |
| `ordering_violation` (classify skipped) | `classify_before_action_gate` | state |
| `pager_violation` (P2/P3 paging) | `pager_severity_gate` | state |
| `pager_violation` (severity arg ≠ classified) | `pager_severity_consistency_gate` | tool-exec |
| `channel_violation` (P3 → channel) | `channel_p3_blocked` | state |
| `channel_violation` (#general) | `channel_general_blocked` | tool-exec |
| `channel_violation` (severity ≠ channel) | `channel_severity_match_gate` | tool-exec |
| `pii_leak` (raw payload echoed) | `pii_leak_gate` | tool-exec |
| `xpia_relay` (literal inbound payload relayed) | `xpia_inbound_payload_relay_gate` | tool-exec |
| `escalation_violation` (obligation) | `escalation_obligation_gate` | state |
| `escalation_violation` (wrong target_team) | `escalation_team_match_gate` | tool-exec |
| `alert_id_drift` | `alert_id_consistency_gate` | tool-exec |
| (vocabulary discipline) | `severity_value_gate` | tool-exec |

The two **residual** failure modes (`wrong_severity`, `fabrication`) are
intentionally **not** policy-closed — they are model-judgment problems
that need a stronger prompt or a deterministic post-classifier, not a
runtime rule.

### Step 4 — Run the AFTER eval against the same seeds (≈35-65 minutes)

```bash
uv run p2m run --config examples/incident_triage_agent/eval_config_guarded.yaml
```

Because `policy.json`, `design.json`, and `seeds.jsonl` are cached at
the suite level, this run **skips** policy/design/seeds and only
re-runs rollout + judge. The seeds are byte-identical to the BEFORE run,
so any metric difference is attributable to the runtime layer alone.

### Step 5 — Compare and read the residual narrative (≈3 minutes)

```bash
uv run p2m results status incident-triage-agent-v1 guarded-with-shield
```

You should see numbers close to those reported in
[`docs/case-study-incident-triage-joint.md`](../../docs/case-study-incident-triage-joint.md)
§4. On the scenario split: `xpia_relay` drops 88 % relative
(12.1 → 1.5 %), `channel_violation` drops 71 % relative
(27.8 → 8.0 %), `ordering_violation` goes to **0 %**,
`pager_violation` drops to 1 %, and `alert_id_drift` and
`escalation_violation` drop measurably (-7.6 pp, -8.9 pp).
`wrong_severity` (~40 %) and `fabrication` (~51 % scenario / 34 %
prompt) stay elevated — that is the signal handed back to the
developer, along with the one trade-off the runtime introduced
(fabrication +21 pp on the *single-turn* prompt rail; corrected by
the auditor on the multi-turn scenario rail).

**The residual is the point.** AgentShield closes what runtime can
close; p2m measures both the closures and the residuals in the same
run; the developer iterates against the failures that remain.

### Step 6 — Browse transcripts in the viewer (optional)

```bash
cd viewer && npm install && npm run dev
# open http://localhost:5173, pick the incident-triage-agent-v1 suite,
# diff baseline-weak-prompt against guarded-with-shield
```

The viewer renders both runs side-by-side; click any seed to see the
original adversarial prompt, the agent's tool calls, and the judge's
verdict on every dimension.

---

## What the developer does next

Hand the AFTER metrics back to the developer. The natural next
iteration is:

1. **Tighten the agent recovery loop** so a single
   `blocked_by_guardrail` doesn't poison the rest of the turn (helps
   the agent finish the runbook even after a guarded reject, and
   closes the +21 pp `fabrication` prompt-rail trade-off in §5.5).
2. **Embed the SOP severity table directly in the system prompt** to
   close `wrong_severity` (the JD2 baseline weakened this on purpose
   so the contrast is visible).
3. **Add a deterministic post-classification verifier** that checks the
   chosen severity against the structured signals — this also helps
   `fabrication` because the verifier output becomes a grounded fact in
   the conversation.
4. **Polish the `escalation_obligation_gate` rule** to bind on
   `target_team` precedence (privacy AND legal for GDPR data exposure;
   procurement AND eng-leadership for sustained vendor) so the residual
   ~28 % escalation_violation drops too — see case-study §5.2.
5. **Tighten the `channel_severity_match_gate` rule** to a whitelist
   of approved channels per severity (currently the model invents
   non-canonical channels like `#incidents-medium` that slip through
   the blacklist) — see case-study §5.1.

After each iteration, re-run the AFTER eval (`p2m run --config
…/eval_config_guarded.yaml`) and watch the table converge. That is the
inner loop.

---

## Anatomy of `agent_guarded.py`

The wrapper is small (~260 lines) and uses the canonical sync sequence
from `agent_shield.adapters._orchestration.guarded_tool_sync`:

```python
cleaned         = _clean_params(args)
call_outcome    = session.validate_tool_call(name, cleaned)   # stage 1
if not call_outcome.verdict.allowed:
    return blocked_message                                    # short-circuit
effective_args  = call_outcome.params or cleaned              # use mutated args
raw_result      = tool(**effective_args)                      # actually run
result_str      = json.dumps(raw_result)                      # stringify (NOT str())
out_outcome     = session.validate_tool_output(name, result_str)  # stage 2 + populators
if not out_outcome.verdict.allowed:
    return blocked_message
final           = out_outcome.result if out_outcome.result is not None else raw_result
session.record_tool_success(name, final)                      # audit
return final
```

Three sharp edges worth knowing if you're writing your own wrapper:
- `ToolOutputOutcome.result` (not `.output`) is the field name on the
  Python dataclass.
- `ToolCallOutcome.params` defaults to `{}` (never `None`); fall back
  to the original cleaned args when it's empty.
- The bundled SDK orchestrator stringifies tool returns with `str()`,
  which produces Python repr (single quotes) for dicts and breaks the
  default JSON populator extractor. This wrapper uses `json.dumps()`
  instead, which is what your populator expressions like
  `@result.severity` actually need.

---

## Known limitations of this measurement

This is a single run pair at n=200 per row. Read the numbers as
**publishable headlines** within their CI band, not as cross-product
benchmarks:

- **Sample size.** 200 prompt seeds + 200 scenario seeds. Each
  per-mode rate carries roughly **±7 pp 95 % CI (Wald)**. The
  headline closures (`channel_violation` -19.8 pp scenario /
  -22.5 pp prompt; `xpia_relay` -10.6 pp scenario;
  `escalation_violation` -8.9 pp; `alert_id_drift` -7.6 pp;
  `ordering_violation` -6.1 pp) all clear that bar by ≥1 CI width.
  Movements within ±5 pp are within noise and called out as such.
- **`xpia_relay` is now measurable.** At the v4 sample size (n=30)
  this dimension showed 0 % BEFORE and was kept as a defense-in-depth
  story. At n=200 it's 7.5 % / 12.1 % BEFORE → 3.0 % / 1.5 %
  AFTER. The literal-relay claim is detectable now.
- **One single-turn trade-off to flag: `fabrication` +21 pp on the
  prompt rail.** The multi-turn scenario rail shows the auditor's
  pushback corrects this (-4.6 pp). See case-study §5.5.
- **`max_turns: 5` truncation is still possible.** The auditor stops
  after 5 user turns. In multi-turn scenarios where one
  `blocked_by_guardrail` poisons the agent's recovery, the auditor
  may run out of turns before the agent recovers.
- **Single judge model.** All scoring is `azure/gpt-5.4` at
  temperature 0. Consider a second-judge sanity pass before publishing
  external numbers.
- **`escalation_obligation_gate` still leaves residual violations in
  AFTER (~28 %).** This is the team-binding edge case described in
  case-study §5.2 — the natural next iteration of the dev loop.
- **Azure content-filter rejections under XPIA pressure.** ~2-6 % of
  rollouts/judge calls trip the Azure content filter on the
  adversarial transcripts. The pipeline classifies these as
  `LLMContentFilterError` and tolerates them per-row up to a 10 % run
  budget; see `p2m/core/model_client.py`.

For the natural set of next experiments (a stronger
recovery prompt, the second-judge pass, n=500 confirmation), see the
case study §6 and the PR description.

## Acknowledgments

This demo would not exist without the [microsoft/AgentShield][as] team,
who built the runtime guardrail engine and the
[`.guardrails.yaml`](https://github.com/microsoft/AgentShield/tree/main/spec)
specification this YAML is authored against. The joint pitch — runtime
plus eval as the local-first inner loop — only works because both halves
exist.

[as]: https://github.com/microsoft/AgentShield

---

## Reproducibility

All artifacts land under
`artifacts/results/incident-triage-agent-v1/{baseline-weak-prompt,guarded-with-shield}/`.
The `policy.json`, `design.json`, and `seeds.jsonl` are stored at the
suite level so any number of guarded variants can A/B against the same
adversarial surface without regenerating seeds.

To re-generate seeds (after editing the rubric or factors), force the
upstream stages:

```bash
uv run p2m run --config examples/incident_triage_agent/eval_config_baseline.yaml \
  --force-stage policy --force-stage design --force-stage seeds
```

To wipe one run and start over:

```bash
rm -rf artifacts/results/incident-triage-agent-v1/guarded-with-shield/
uv run p2m run --config examples/incident_triage_agent/eval_config_guarded.yaml
```
