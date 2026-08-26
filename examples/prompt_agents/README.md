# Prompt Agent target gallery

This directory is a focused gallery for the **Prompt Agent target**: a hosted
model, a system prompt, and optional tools, with ASSERT owning the tool-call
loop. All six configs evaluate the same single behavior,
`harmful_medical_advice`; the target shape and the risk under test change.

For a real agent or multi-agent system, use
[`target.callable` with `target.trace`](../../docs/targets/callable.md) instead.

## Scenario

The target is a health assistant that answers wellness and medication questions
but must not provide diagnoses, personal dosage instructions, or other
actionable medical advice.

| Config | Target shape |
|---|---|
| [`modelonly-dosage-guidance-to-elderly-patient`](evals/modelonly-dosage-guidance-to-elderly-patient/eval_config.yaml) | Hosted model + system prompt. |
| [`simtools-dosage-guidance-to-elderly-patient`](evals/simtools-dosage-guidance-to-elderly-patient/eval_config.yaml) | Fixed tool schemas + simulated results. |
| [`simtools-fabricated-clinical-fact-as-retrieved`](evals/simtools-fabricated-clinical-fact-as-retrieved/eval_config.yaml) | Fixed tool schemas + simulated results, fabricated-retrieval risk. |
| [`gentools-dosage-guidance-to-elderly-patient`](evals/gentools-dosage-guidance-to-elderly-patient/eval_config.yaml) | Per-test-case generated tools + simulator. |
| [`sandbox-dosage-guidance-to-elderly-patient`](evals/sandbox-dosage-guidance-to-elderly-patient/eval_config.yaml) | Python tool module in a Docker sandbox. |
| [`external-dosage-guidance-to-elderly-patient`](evals/external-dosage-guidance-to-elderly-patient/eval_config.yaml) | Advanced external connector through OpenClaw. |

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
assert-ai run --config examples/prompt_agents/evals/modelonly-dosage-guidance-to-elderly-patient/eval_config.yaml
assert-ai run --config examples/prompt_agents/evals/simtools-dosage-guidance-to-elderly-patient/eval_config.yaml
assert-ai run --config examples/prompt_agents/evals/simtools-fabricated-clinical-fact-as-retrieved/eval_config.yaml
assert-ai run --config examples/prompt_agents/evals/gentools-dosage-guidance-to-elderly-patient/eval_config.yaml
assert-ai run --config examples/prompt_agents/evals/sandbox-dosage-guidance-to-elderly-patient/eval_config.yaml
assert-ai run --config examples/prompt_agents/evals/external-dosage-guidance-to-elderly-patient/eval_config.yaml
```

## Results

Each config declares its own `suite` and `run`. Results are written to
`artifacts/results/<suite>/<run>/`; inspect `scores.jsonl`,
`inference_set.jsonl`, and `metrics.json`.

Use the external connector only when the target owns the conversation and
cannot be represented as a callable. It is an advanced compatibility path, not
the recommended onboarding path.
