# Incident-triage agent

A self-contained SRE incident-triage agent that reads an alert, classifies its
severity, dispatches notifications, files a ticket, and escalates when required.
The agent follows [`SOP.md`](SOP.md) and is evaluated as a callable target with
OpenTelemetry traces, so the judge can inspect tool order and arguments.

The backend uses synthetic fixtures only. No PagerDuty, Slack, ticketing
service, or Docker setup is required.

## Files

| Path | Purpose |
|---|---|
| `agent.py` | LiteLLM tool loop exposing `chat` and the deliberately permissive `chat_naive`. |
| `SOP.md` | Severity, routing, redaction, and escalation rules. |
| `fixtures/` | Ten synthetic alerts and the team roster. |
| `evals/*.yaml` | Nine independently runnable, one-behavior eval configs. |
| `evals/README.md` | Behavior index and run instructions. |

## Tools

| Tool | Purpose |
|---|---|
| `get_alert` | Read alert metadata. |
| `classify_severity` | Persist a P0/P1/P2/P3 classification. |
| `page_oncall` | Page on-call for P0/P1 alerts. |
| `notify_channel` | Post a redacted alert update to the correct channel. |
| `update_ticket` | Append an audit note. |
| `escalate_to_manager` | Route cross-team escalation signals. |

## Behaviors

The configs isolate SOP ordering, pager discipline, channel routing, PII
redaction, retrieved prompt-injection resistance, escalation correctness,
alert-ID integrity, severity classification, and fabrication.

## Setup and run

From the repo root:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[otel]"
cp examples/incident_triage_agent/.env.example examples/incident_triage_agent/.env
# Set AZURE_API_KEY and AZURE_API_BASE.

assert-ai run --config examples/incident_triage_agent/evals/pii_redaction.yaml
```

`INCIDENT_TRIAGE_MODEL` optionally overrides the agent model. No other service
credentials are required.

## Results

For the command above, results land in
`artifacts/results/incident-triage-pii-redaction-v1/baseline/`. Inspect:

1. `scores.jsonl` for judge verdicts and evidence.
2. `inference_set.jsonl` for tool calls, order, and arguments.
3. `metrics.json` for aggregate rates.
4. The suite-level `test_set.jsonl` for generated test cases.

Run any other config in [`evals/`](evals/) the same way. Each declares a
distinct suite, so behaviors can be regenerated and compared independently.

All fixture credential-shaped strings are fake test values. Generated
artifacts and local `.env` files remain uncommitted.
