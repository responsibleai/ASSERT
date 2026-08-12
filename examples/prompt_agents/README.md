# Prompt Agent target gallery

This directory is a focused gallery for the **Prompt Agent target**: a hosted
model, a system prompt, and optional tools, with ASSERT owning the tool-call
loop. All five configs evaluate the same single behavior,
`harmful_medical_advice`; only the target shape changes.

For a real agent or multi-agent system, use
[`target.callable` with `target.trace`](../../docs/targets/callable.md) instead.

## Scenario

The target is a health assistant that answers wellness and medication questions
but must not provide diagnoses, personal dosage instructions, or other
actionable medical advice.

| Config | Target shape |
|---|---|
| [`health_assistant.yaml`](health_assistant.yaml) | Hosted model + system prompt. |
| [`health_assistant_simulated_tools.yaml`](health_assistant_simulated_tools.yaml) | Fixed tool schemas + simulated results. |
| [`health_assistant_sandbox.yaml`](health_assistant_sandbox.yaml) | Python tool module in a Docker sandbox. |
| [`health_assistant_generated_tools.yaml`](health_assistant_generated_tools.yaml) | Per-test-case generated tools + simulator. |
| [`health_assistant_external.yaml`](health_assistant_external.yaml) | Advanced external connector through OpenClaw. |

Shared tool definitions live in [`../agents/`](../agents/).

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[otel]"
cp .env.example .env
# Set AZURE_API_BASE and AZURE_API_KEY.
```

PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[otel]"
Copy-Item .env.example .env
```

Docker Desktop is required for the sandbox and external-connector configs.

## Run

```bash
assert-ai run --config examples/prompt_agents/health_assistant.yaml
assert-ai run --config examples/prompt_agents/health_assistant_simulated_tools.yaml
assert-ai run --config examples/prompt_agents/health_assistant_sandbox.yaml
assert-ai run --config examples/prompt_agents/health_assistant_generated_tools.yaml
assert-ai run --config examples/prompt_agents/health_assistant_external.yaml
```

## Results

Each config declares its own `suite` and `run`. Results are written to
`artifacts/results/<suite>/<run>/`; inspect `scores.jsonl`,
`inference_set.jsonl`, and `metrics.json`.

Use the external connector only when the target owns the conversation and
cannot be represented as a callable. It is an advanced compatibility path, not
the recommended onboarding path.
