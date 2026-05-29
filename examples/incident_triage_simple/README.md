# Incident-triage agent (simplified) — joint AgentShield + ASSERT demo

> **Joint pitch.** ASSERT lets developers stay lazy at authoring time (NL → comprehensive eval); Agent Shield lets enforcement be deterministic at runtime (structured-attribute gates first, LLM classifiers second); together they close the loop — ASSERT finds the failure space, Agent Shield closes the structurally-fixable subset, ASSERT re-measures the residual.

Three alerts, four tools, ~85-line `guardrails.yaml`. Same loop as the full case study (`../incident_triage_agent/`) but small enough to read in one sitting.

## Rules sorted by enforcement type

| Type | Rule | What it does |
|---|---|---|
| **Deterministic** (headline) | `alert_must_be_loaded_gate` | Blocks `post_to_channel` / `escalate` until `get_alert` returned a real alert. State predicate, not a classifier. |
| **Deterministic** (headline) | `no_payload_relay_in_channel` | Blocks `post_to_channel` if `message` contains the alert's `inbound_payload_text` verbatim. Data-flow taint, not a classifier. |
| **HITL** | `p0_escalate_requires_oncall_ack` | Blocks `escalate` on a P0 alert unless `acknowledge_oncall_page` was called first. Forces a human handshake before high-severity action. |
| **Auxiliary** (supporting, not headline) | `customer_summary_pii_redaction` | Python-side LLM classifier in `agent_guarded.py`. WARNS (never blocks) when a `post_to_channel.message` paraphrases customer PII the literal-relay gate would miss. Surfaces a `aux_warning` field in the tool result + an OTel span. |

`reasonable_escalation` stays outside the runtime — it's tester-scored model judgment, not a deterministic rule.

## How to run

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[otel]"
cp .env.example .env

# BEFORE — bare agent
assert-eval run --config examples/incident_triage_simple/eval_config.yaml --run before

# AFTER — switch target.callable in eval_config.yaml to
# examples.incident_triage_simple.agent_guarded:chat, then rerun
python -m pip install agent-shield        # required for the AFTER run only
assert-eval run --config examples/incident_triage_simple/eval_config.yaml --run after
```

The published config defaults to 10 total test cases (`prompt.sample_size: 5` + `scenario.sample_size: 5`) so each BEFORE or AFTER run should finish in under 5 minutes. To reproduce the previous larger simple sweep, add `--override test_set.sample_size=24`; expect about 5 minutes per run.

To disable the aux classifier (e.g. offline/no-Azure): `INCIDENT_TRIAGE_AUX_DISABLED=1`.

## What you should see

| Behavior | BEFORE | AFTER |
|---|---|---|
| `followed_alert_first` | Fails on XPIA-style cases (skips `get_alert`) | Passes — deterministic gate forces order |
| `no_payload_relay` | Fails when agent copies attacker text | Passes — deterministic gate blocks the relay |
| `hitl_oncall_ack_before_p0_escalate` | Fails on P0 escalations (agent forgets the ack tool) | Passes — HITL gate forces the handshake |
| `reasonable_escalation` | Model-judged residual | Same — left to developer iteration |
| Aux: paraphrased-PII warnings | None | Surfaced in `aux_warning` field + OTel span (supporting evidence, not enforcement) |

That is the eval-and-fix loop in one folder.

## See also

- `../incident_triage_agent/README.md` — the full case study with 10 alerts, 6 tools, 13 gates, statistical n=200 measurement.
