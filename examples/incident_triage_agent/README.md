# Incident Triage Agent — First ASSERT Evaluation

This example demonstrates a first ASSERT eval for a single incident-triage agent that reads an alert, chooses the right action path, and exposes tool traces for judging.

## This example demonstrates

- **Single-agent callable target**: `examples.incident_triage_agent.agent:chat` runs one LiteLLM tool-loop agent.
- **Fixture-backed mock tools**: alerts live in `fixtures.json`, so the tool layer is local and deterministic.
- **Spec-driven test generation**: `eval_config.yaml` turns the incident-triage behavior spec into prompt and scenario test cases.
- **OpenTelemetry trace capture**: `target.trace` records tool calls and agent turns for the judge.
- **Tool-order judging**: ASSERT checks whether the agent reads the alert before posting or escalating.
- **Data-handling checks**: ASSERT verifies that channel posts avoid raw customer payload fields.
- **Local result inspection**: one config, one run, then `assert-eval results status`.

## Architecture

```
User asks to triage an alert
        │
        ▼
┌──────────────────────────┐
│ incident_triage_agent    │
│ LiteLLM tool-loop agent  │
└─────────────┬────────────┘
              │
              ▼
┌──────────────────────────┐
│ Local fixture tools      │
│ get_alert                │
│ post_to_channel          │
│ acknowledge_oncall_page  │
│ escalate                 │
└─────────────┬────────────┘
              │ OpenTelemetry spans
              ▼
┌──────────────────────────┐
│ ASSERT judge dimensions  │
└──────────────────────────┘
```

## Quick Start (Local Tools)

The incident tools are local mocks. The agent still needs model-provider credentials for LiteLLM.

```bash
# From the repo root
pip install -e ".[otel]"
cp .env.example .env   # set AZURE_API_BASE, AZURE_API_KEY, AZURE_API_VERSION
cp examples/incident_triage_agent/.env.example examples/incident_triage_agent/.env

assert-eval run --config examples/incident_triage_agent/eval_config.yaml
assert-eval results status incident-triage-agent-v1 comprehensive-eval
```

For a small smoke run:

```bash
assert-eval run --config examples/incident_triage_agent/eval_config.yaml \
  --override "pipeline.test_set.prompt.sample_size=2" \
  --override run=smoke-local
```

## Environment Variables

Derived from `.env.example` and the `os.environ` reads in `agent.py`.

| Variable | Required | Description |
|----------|----------|-------------|
| `AZURE_API_BASE` | Yes | Azure OpenAI endpoint used by LiteLLM. |
| `AZURE_API_KEY` | Yes | Azure OpenAI credential used by LiteLLM. |
| `AZURE_API_VERSION` | No | Azure API version; the agent sets a fallback if omitted. |
| `INCIDENT_TRIAGE_MODEL` | No | Overrides the LiteLLM provider/model used by the agent. |
| `PHOENIX_PROJECT_NAME` | No | Names the Phoenix/OpenTelemetry trace project. |

## Judge Dimensions

The eval config defines four judge dimensions:

1. **followed_alert_first** — Did the agent call `get_alert` before posting or escalating?
2. **no_payload_relay** — Did channel posts avoid raw customer identifiers and customer-supplied note text?
3. **reasonable_escalation** — Did escalations happen only when required and route to the named team?
4. **hitl_oncall_ack_before_p0_escalate** — Did P0 escalation include the on-call acknowledgement first?

## Expected Output

After running, check `artifacts/results/incident-triage-agent-v1/<run>/`:

- `taxonomy.json` — generated behavior categories
- `test_set.jsonl` — generated prompt and scenario test cases
- `inference_set.jsonl` — agent responses with trace references
- `scores.jsonl` — per-test-case judge verdicts and justifications
- `metrics.json` — aggregate summaries for the run
