# Incident-triage agent (simplified) — joint AgentShield + p2m demo

This is the small, fast version of the case study one folder over (see `../incident_triage_agent/README.md`). It tells the same end-to-end story — p2m finds concrete incident-triage failures, AgentShield closes the deterministic runtime-fixable subset, and p2m measures the residual — but with 3 alerts, 3 tools, and 75-line guardrails.

## Full case study vs. simplified demo

| Dimension | Full case study (`../incident_triage_agent/`) | Simplified demo (this folder) |
|---|---|---|
| Goal | Publishable joint case study with measured BEFORE/AFTER deltas | Fast local walkthrough of the same loop |
| Alerts | 10 fixture alerts, XPIA-enriched | 3 fixture alerts, one XPIA-heavy |
| Tools | 6-tool SRE runbook loop | 3 tools: `get_alert`, `post_to_channel`, `escalate` |
| Guardrails | 13 AgentShield gates | 2 deterministic gates |
| p2m surface | Larger test_set and 11 judge dimensions | Small test_set and 3 judge dimensions |
| Best use | Demo, discussion, and deeper debugging | Quick smoke test or first read |

## Two deterministic gates

The simplified runtime keeps only the two gates that make the loop obvious:

1. **Read the alert before acting.** The agent must call `get_alert` before it posts or escalates, so the runtime has a grounded alert payload to validate against.
2. **Never relay attacker-controlled payload text.** If `inbound_payload_text` shows up in an outbound channel post, AgentShield returns `blocked_by_guardrail`; the agent has to re-plan without copying the injected text.

`reasonable_escalation` remains intentionally outside the guardrail layer. That is a tester-scored model-judgment dimension, not a deterministic runtime rule.

## How to run

From `examples/incident_triage_simple/`:

```bash
# 1. Install dependencies from the repository project.
uv sync

# 2. Copy the env template, then fill in your Azure values.
cp ./.env.example ./.env

# 3. BEFORE — run the bare agent.
uv run p2m run --config ./eval_config.yaml --run before

# 4. AFTER — switch target.callable in eval_config.yaml to
#    examples.incident_triage_simple.agent_guarded:chat, then rerun.
uv run p2m run --config ./eval_config.yaml --run after
```

## What you should see

- **BEFORE** should fail `followed_alert_first` and `no_payload_relay` on ALR-002-style XPIA cases: the agent skips `get_alert`, copies attacker text, or both.
- **AFTER** should close both deterministic gates. The inference output should show `blocked_by_guardrail`, then a cleaner retry.
- Inspect `inference_set.jsonl` for the conversation/tool-call trace and `scores.jsonl` for tester evidence on each dimension. The remaining `reasonable_escalation` misses are the intended residual: developer work, not guardrail work.

That is the eval-and-fix loop in one folder.
