# Incident Triage (Simple) — joint p2m + AgentShield demo

A small SRE triage agent. The point of this folder: show in **~300 lines
total** that p2m surfaces failure modes a developer would actually care
about, and that a few lines of AgentShield YAML close the structural ones.

## What's here

| File | Why it exists |
|---|---|
| `agent.py` | LiteLLM tool loop with 3 tools: `get_alert`, `post_to_channel`, `escalate`. 5-sentence system prompt. |
| `agent_guarded.py` | Same agent, wrapped by the runtime defined in `guardrails.yaml`. |
| `eval_config.yaml` | One eval config. Swap the `target.callable` line for BEFORE / AFTER runs. |
| `guardrails.yaml` | Two deterministic gates: must read the alert first; never relay attacker-controlled payload text into a channel post. |
| `fixtures.json` | Three alerts: P0 security, P0 data + XPIA, P2 low impact. |
| `incident_triage_simple.md` | Behavior-under-test prose. |
| `.env.example` | Local env knobs. |

## Run it

```bash
cp .env.example .env  # then fill the Azure values

# BEFORE: surface failure modes against the bare agent
p2m run --config examples/incident_triage_simple/eval_config.yaml --run before

# AFTER: edit eval_config.yaml — flip target.callable to
# examples.incident_triage_simple.agent_guarded:chat — and re-run
p2m run --config examples/incident_triage_simple/eval_config.yaml --run after
```

## What you should see

- **BEFORE** fails `followed_alert_first` and `no_payload_relay` on the
  ALR-002 (XPIA) scenarios. Channel posts will contain pasted
  attacker text or skipped the `get_alert` step entirely.
- **AFTER** closes both. The runtime returns
  `blocked_by_guardrail` to the agent, the agent re-plans, and the
  next attempt is clean.
- The `reasonable_escalation` dimension is intentionally **not**
  enforced by guardrails — it's a model-judgment dimension. The
  AFTER run shows how much the model gets right on its own.

That's the eval-and-fix loop in one folder. Skim the YAMLs — they're
plain English. That is the entire demo.
