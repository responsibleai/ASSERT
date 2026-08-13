# Incident-triage behaviors

Each YAML in this directory measures one independently actionable behavior,
uses one custom judge dimension, and writes to its own suite.

```bash
assert-ai run --config examples/incident_triage_agent/evals/pii_redaction.yaml
assert-ai results status incident-triage-pii-redaction-v1 baseline
```

| Config | Behavior | What it checks |
|---|---|---|
| `sop_tool_ordering.yaml` | `sop_tool_ordering` | Classification completes before action tools run. |
| `pager_severity_discipline.yaml` | `pager_severity_discipline` | Paging occurs only for P0/P1. |
| `channel_severity_routing.yaml` | `channel_severity_routing` | Posts go only to severity-approved channels. |
| `pii_redaction.yaml` | `pii_redaction` | Customer PII and secrets are redacted before posting. |
| `xpia_resistance.yaml` | `xpia_resistance` | Alert payload text is treated as data, not instructions. |
| `escalation_correctness.yaml` | `escalation_correctness` | Escalation signals route to the correct team. |
| `alert_id_integrity.yaml` | `alert_id_integrity` | Tool calls remain pinned to the fetched alert ID. |
| `severity_classification.yaml` | `severity_classification` | Severity follows the SOP decision tree. |
| `no_fabrication.yaml` | `no_fabrication` | Concrete claims are grounded in tool output. |

Artifacts land under `artifacts/results/<suite>/baseline/`. To compare prompt
quality, copy one config, give it a new `run`, and point `target.callable` at
`examples.incident_triage_agent.agent:chat_naive`.
