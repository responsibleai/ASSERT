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

1. **What is broken** (p2m surfaces 8 failure modes, each scored on a
   structured rubric).
2. **What the runtime layer can fix** (AgentShield closes 6 of the 8
   procedural modes, verified by re-running the same seeds).
3. **What is left for the developer** (2 model-judgment modes —
   `wrong_severity`, `fabrication` — that no policy YAML can honestly
   close, plus a *new* trade-off: `overrefusal` jumps when the runtime
   blocks more aggressively than the model wants to behave).

---

## File layout

```
examples/incident_triage_agent/
├── README.md                                # this file
├── SOP.md                                   # the runbook (single source of truth)
├── incident_triage_workflow_failures.md     # p2m concept (8 failure modes)
├── incident-triage.guardrails.yaml          # AgentShield policy (6 rules)
├── .env.example                             # demo-specific env overrides
├── agent.py                                 # baseline agent (no guardrails)
├── agent_guarded.py                         # same agent, AgentShield-wrapped
├── eval_config_baseline.yaml                # p2m: target = agent:chat
├── eval_config_guarded.yaml                 # p2m: target = agent_guarded:chat
└── fixtures/
    ├── incidents.json                       # 10 mock alerts (covers all branches)
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

## The walk-through (about 25 minutes wall clock)

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

### Step 2 — Run the BEFORE eval (≈12 minutes)

```bash
uv run p2m run --config examples/incident_triage_agent/eval_config_baseline.yaml
```

This generates a behavior taxonomy from the 8 failure modes, designs 30
prompt seeds + 6 multi-turn scenario seeds, runs them against
`agent:chat` (the unguarded baseline), and scores every transcript with
`azure/gpt-5.4` against the 10-dimension rubric.

Inspect the headline metrics:

```bash
uv run p2m results status incident-triage-agent-v1 baseline-weak-prompt
```

You should see a `policy_violation` rate in the **63–73%** range and
non-zero rates on most of the 8 failure-mode dimensions.

### Step 3 — Read the policy you're about to apply (≈5 minutes)

Open
[`incident-triage.guardrails.yaml`](incident-triage.guardrails.yaml) and
walk through the six rules:

| Rule | Closes failure mode |
|---|---|
| `alert_must_be_loaded` (predicate gate on every action tool) | `ordering_violation` |
| `severity_match` (predicate on `page_oncall.severity` and `notify_channel.channel`) | `pager_violation`, `channel_violation` |
| `payload_redaction` (mutation on `notify_channel.message`) | `pii_leak` |
| `escalation_required` (obligation on `escalate_to_manager.target_team`) | `escalation_violation` |
| `alert_id_consistency` (post-tool check) | `alert_id_drift` |

The two **residual** failure modes (`wrong_severity`, `fabrication`) are
intentionally **not** policy-closed — they are model-judgment problems
that need a stronger prompt or a deterministic post-classifier, not a
runtime rule.

### Step 4 — Run the AFTER eval against the same seeds (≈12 minutes)

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

You should see numbers close to these (single run, n=30 prompt seeds):

| Failure mode | BEFORE | AFTER | Δ | What it means |
|---|---:|---:|---:|---|
| `channel_violation` | 36.7% | **3.3%** | −33.4 pp | ✅ closed by `severity_match` |
| `pii_leak` | 6.7% | **0.0%** | −6.7 pp | ✅ closed by `payload_redaction` |
| `ordering_violation` | 3.3% | **0.0%** | −3.3 pp | ✅ closed by `alert_must_be_loaded` |
| `escalation_violation` | 40.0% | 36.7% | −3.3 pp | ⚠️ partial — team-binding edge case |
| `wrong_severity` (residual) | 46.7% | 46.7% | 0 pp | ⚪ as designed — model judgment |
| `fabrication` (residual) | 40.0% | 36.7% | −3.3 pp | ⚪ as designed — grounding |
| **`overrefusal`** (new!) | 30.0% | **60.0%** | **+30.0 pp** | 🔥 the load-bearing finding |
| `policy_violation` (OR) | 73.3% | 63.3% | −10.0 pp | dragged down by residuals |

**The overrefusal jump is the point.** The runtime didn't just close
violations — it introduced a new failure mode (the agent now refuses
some legitimate requests). p2m surfaces this as a measurable rubric
dimension. Without the eval, the developer would have shipped a "more
secure" agent that silently helps customers less.

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
   `blocked_by_guardrail` doesn't poison the rest of the turn (drives
   `overrefusal` down — the §5.3 trade-off in the case study).
2. **Embed the SOP severity table directly in the system prompt** to
   close `wrong_severity` (the JD2 baseline weakened this on purpose
   so the contrast is visible).
3. **Add a deterministic post-classification verifier** that checks the
   chosen severity against the structured signals — this also helps
   `fabrication` because the verifier output becomes a grounded fact in
   the conversation.
4. **Polish the `escalation_required` rule** to bind on `target_team`
   instead of "an escalation happened" so the residual ~37% drops too.

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
