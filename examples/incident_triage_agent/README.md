# Incident Triage Agent — joint AgentShield + p2m demo

This demo showcases the **local-first developer eval-fix loop** that no
single tool ships today: p2m (spec-driven test harness) discovers the
agent's failure modes; AgentShield (runtime guardrails) closes the
policy-fixable subset; the residual failures go back to the developer
to fix in code or prompt.

## What you get

- **`SOP.md`** — the canonical runbook the agent is supposed to follow
  (5 steps: read alert → classify severity → notify → update ticket →
  escalate). This document is the single source of truth for the
  agent's system prompt, the .guardrails.yaml policy, and the eval
  judge rubric. Crucially, the rubric is a **decision tree on
  structured alert signals**, not prose, so the judge can adjudicate
  deterministically.
- **`incident_triage_workflow_failures.md`** — the p2m concept file
  enumerating **8 failure modes** in two groups:
  - **6 procedural / runtime-enforceable**: skipped severity check,
    unauthorized pager use, wrong-channel posts, PII in channel
    messages, missed escalation when a signal is set, alert-ID drift.
  - **2 model-judgment / residual**: wrong severity classification,
    semantic fabrication / ungrounded synthesis.
  p2m's policy stage expands this into a behavior taxonomy; the seeds
  stage generates adversarial test cases against it.
- **`agent.py`** — the **baseline** agent (no guardrails). Direct
  LiteLLM tool-calling loop against Azure `gpt-5.4-mini`, with 6 mock
  tools driven by `fixtures/incidents.json`. No framework dependency
  (no LangChain) — keeps the demo legible and the AgentShield wrap
  surgical.
- **`agent_guarded.py`** — the **guarded** agent. Same logic, wrapped
  with the AgentShield Python SDK 0.13.0 loading
  `incident-triage.guardrails.yaml`. Approval gates are simulated by a
  manager-judge LLM (`INCIDENT_TRIAGE_MANAGER_JUDGE_MODEL`).
- **`incident-triage.guardrails.yaml`** — the AgentShield policy.
  Six rules — one per runtime-enforceable failure mode (severity
  precondition, pager-severity binding, channel-severity binding,
  PII/secrets scan on channel messages, escalation-signal binding,
  alert-ID drift detection across tool calls). The two residual modes
  (wrong severity classification, semantic fabrication) are
  intentionally **not** policy-closed — they highlight where the
  developer must change the prompt, model, or grounding strategy.
- **`eval_config_baseline.yaml`** / **`eval_config_guarded.yaml`** — the
  p2m configs. Identical except for the `target.callable` import path.
  Run both, compare metrics.

## Run the demo

```bash
# 1. Install AgentShield (one-time, see top-level README for the wheel URL)
uv pip install /path/to/agent_shield-0.13.0a1-cp311-abi3-win_amd64.whl

# 2. Copy the env template
cp examples/incident_triage_agent/.env.example examples/incident_triage_agent/.env

# 3. Smoke-test both agents
uv run python examples/incident_triage_agent/agent.py
uv run python examples/incident_triage_agent/agent_guarded.py

# 4. Run the BEFORE eval (baseline agent, no guardrails)
uv run p2m run --config examples/incident_triage_agent/eval_config_baseline.yaml

# 5. Run the AFTER eval (guarded agent, same seeds)
uv run p2m run --config examples/incident_triage_agent/eval_config_guarded.yaml

# 6. Inspect the comparison
cd viewer && npm run dev
```

## Expected results

| Failure mode | Stage | Baseline | Guarded | Delta |
|---|---|---|---|---|
| Skipped severity classification | S2 state | high | **near-0%** | closed by precondition |
| Unauthorized pager use (P2/P3 paging) | S3 tool | high | **near-0%** | closed by tool-arg gate |
| Wrong-channel post (severity ↔ channel mismatch) | S3 tool | medium | **near-0%** | closed by tool-arg binding |
| PII / secrets in channel message | S3 tool | medium | **near-0%** | closed by argument scan |
| Missed escalation when signal is set | S2 / S4 | medium | **low** | closed by signal-bound obligation |
| Alert ID drift across tool calls | S4 post-tool | low–medium | **near-0%** | closed by ID-equality check |
| **Wrong severity classification** | residual | medium | **medium** | **NOT CLOSED — back to dev** |
| **Semantic fabrication / ungrounded synthesis** | residual | medium | **medium** | **NOT CLOSED — back to dev** |

Overall violation rate is expected to fall from ~40–60% (baseline) to
~10–20% (guarded). The remaining violations are concentrated in the two
residual modes.

The two residuals are the joint pitch: p2m surfaces failures the runtime
cannot honestly close. **Wrong severity classification** is a model
judgment problem (fix: better prompting, few-shot examples, a stronger
classification model). **Semantic fabrication** is a grounding problem
(fix: tighter system prompt about citation, lower temperature, or
retrieval over allowed claims). A runtime policy can verify *that*
classify was called and *that* a claimed alert ID was actually fetched,
but it cannot adjudicate which severity was correct or whether a
plausible-sounding root-cause narrative was actually grounded in the
tool history. That residual is exactly where the developer's next
iteration belongs.

## File layout

```
examples/incident_triage_agent/
├── README.md                                # this file
├── SOP.md                                   # runbook (system-prompt source)
├── incident_triage_workflow_failures.md     # p2m concept (failure-mode source)
├── incident-triage.guardrails.yaml          # AgentShield policy
├── .env.example                             # local env template
├── agent.py                                 # baseline (no guardrails)
├── agent_guarded.py                         # guarded (AgentShield-wrapped)
├── eval_config_baseline.yaml                # p2m: target.callable=…agent:chat
├── eval_config_guarded.yaml                 # p2m: target.callable=…agent_guarded:chat
└── fixtures/
    ├── incidents.json                       # mock alert DB (10 alerts)
    └── teams.json                           # mock team / on-call roster
```
