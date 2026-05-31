# Prompt Agent examples

These examples show the **Prompt Agent target**: a hosted model + a system prompt + an optional tool schema, with the runtime owning the tool-call loop. Use them to smoke-test a prompt, iterate on a toolset before code exists, or compare simulated and real tool execution.

For a real agent or multi-agent system, start with the callable target and `target.trace` instead. The flagship path is [`../travel_planner_langgraph/`](../travel_planner_langgraph/), where OpenTelemetry traces let the judge see tool calls, routing, and intermediate decisions.

## Scenario

The scenario is a health assistant evaluated for **harmful medical advice**. The assistant helps with wellness questions, medication information, and appointment scheduling, but must not provide diagnoses, dosage instructions, or other actionable medical guidance.

The five configs exercise different Prompt Agent options around the same failure mode:

| Config | Target shape | What it demonstrates |
|---|---|---|
| [`health_assistant.yaml`](health_assistant.yaml) | Hosted model + system prompt | Smallest smoke test: no tools, just the model behavior and judge loop. |
| [`health_assistant_simulated_tools.yaml`](health_assistant_simulated_tools.yaml) | Hosted model + fixed toolset + simulator | Tool schemas from [`health_assistant_tools.yaml`](health_assistant_tools.yaml); an LLM simulator returns tool results. |
| [`health_assistant_sandbox.yaml`](health_assistant_sandbox.yaml) | Hosted model + Python tool module | Real tool functions from [`health_assistant.py`](health_assistant.py), executed in a Docker-backed sandbox per conversation. |
| [`health_assistant_generated_tools.yaml`](health_assistant_generated_tools.yaml) | Hosted model + per-test-case tools + simulator | Each generated test case carries its own tool definitions; the simulator returns plausible results. |
| [`health_assistant_external.yaml`](health_assistant_external.yaml) | External connector | Advanced/legacy connector path through [`openclaw/`](openclaw/), with the external agent owning the conversation. |

## Value-add

Prompt Agent evals catch issues while the agent surface is still cheap to change:

- harmful, actionable medical advice that should have been refused or redirected to a clinician
- unsafe use of medication lookup, dosage, or patient-profile results
- missing or ambiguous tool descriptions, arguments, and selection boundaries
- prompt regressions before a real tool backend or orchestration layer exists

> **TDD progression:** “you can test the prompt and toolset design before any agent code is written.” Start with [`health_assistant_simulated_tools.yaml`](health_assistant_simulated_tools.yaml) to iterate on the system prompt + toolset. When the tools are implemented, swap `tools.toolset` + `tools.simulator` for `tools.module` in [`health_assistant_sandbox.yaml`](health_assistant_sandbox.yaml). The eval spec, test generation, and judge stay the same.

Use these demos for Prompt Agent smoke tests, TDD on prompts and toolsets, and simple model-only evals. Do not use them as a substitute for tracing a real agent framework. Once your code owns routing, planning, sub-agents, or tool execution, use [`target.callable` with `target.trace`](../../docs/targets/callable.md). For the full target decision tree, see [`docs/targets/`](../../docs/targets/README.md).

## How to use

From the repo root, install the package and configure your model provider first:

```bash
python -m pip install -e ".[otel]"
cp .env.example .env
# Set AZURE_API_BASE and AZURE_API_KEY. Adjust model names in YAML if you use a non-Azure LiteLLM provider.
```

PowerShell equivalent:

```powershell
python -m pip install -e ".[otel]"
Copy-Item .env.example .env
# Set AZURE_API_BASE and AZURE_API_KEY. Adjust model names in YAML if you use a non-Azure LiteLLM provider.
```

Run any config with `assert-eval`:

| Config | `assert-eval` |
|---|---|
| Model only | `assert-eval run --config examples/prompt_agents/health_assistant.yaml` |
| Simulated tools | `assert-eval run --config examples/prompt_agents/health_assistant_simulated_tools.yaml` |
| Sandbox tool module | `assert-eval run --config examples/prompt_agents/health_assistant_sandbox.yaml` |
| Generated tools | `assert-eval run --config examples/prompt_agents/health_assistant_generated_tools.yaml` |
| External connector | `assert-eval run --config examples/prompt_agents/health_assistant_external.yaml` |

**Docker prerequisite:** [`health_assistant_sandbox.yaml`](health_assistant_sandbox.yaml) and [`health_assistant_external.yaml`](health_assistant_external.yaml) start containers per conversation. Keep Docker Desktop running before invoking them. The sandbox variant may pull `python:3.11-bookworm`; the external connector builds an OpenClaw image from [`openclaw/Dockerfile`](openclaw/Dockerfile).

### Files

| File | What it does |
|---|---|
| [`harmful_medical_advice.md`](../behavior_specs/harmful_medical_advice.md) | Eval spec used by the health-assistant configs. |
| [`health_assistant.yaml`](health_assistant.yaml) | Hosted-model smoke test with a system prompt and no tools. |
| [`health_assistant_simulated_tools.yaml`](health_assistant_simulated_tools.yaml) | Prompt Agent with fixed tool schemas and simulated results. |
| [`health_assistant_sandbox.yaml`](health_assistant_sandbox.yaml) | Prompt Agent with real Python tools via `tools.module: examples.prompt_agents.health_assistant`. |
| [`health_assistant_generated_tools.yaml`](health_assistant_generated_tools.yaml) | Prompt Agent where generated test cases provide tool definitions. |
| [`health_assistant_external.yaml`](health_assistant_external.yaml) | External connector example for OpenClaw. |
| [`health_assistant.py`](../agents/health_assistant.py) | Docker-backed tool module: medication lookup, interaction checks, dosage assessment, and patient profile. |
| [`health_assistant_tools.yaml`](../agents/health_assistant_tools.yaml) | Toolset schema for simulator-backed runs. |
| [`openclaw/`](../agents/openclaw/) | Docker assets and connector for the advanced external-connector path. |

### When to use the external-connector path

Use [`../agents/openclaw/`](../agents/openclaw/) only when you need to evaluate an external process that owns the conversation and cannot be represented as a callable. This is the advanced/legacy path. For new customer onboarding, prefer `target.callable` with trace capture; it is simpler, easier to debug, and gives the judge better evidence.

## Behavior violation rate results

Not yet measured at `n=10` after this reorganization. Do not treat the configs as benchmark results until you run them with a fixed model, seed, and sample size.

| Config | Sample size | Behavior violation rate |
|---|---:|---:|
| `health_assistant.yaml` | Not yet measured | TBD |
| `health_assistant_simulated_tools.yaml` | Not yet measured | TBD |
| `health_assistant_sandbox.yaml` | Not yet measured | TBD |
| `health_assistant_generated_tools.yaml` | Not yet measured | TBD |
| `health_assistant_external.yaml` | Not yet measured | TBD |
