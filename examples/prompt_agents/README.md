# Prompt Agent examples

These examples show the **Prompt Agent target**: a hosted model + a system prompt + an optional tool schema, with the runtime owning the tool-call loop. Use them to smoke-test a prompt, iterate on a toolset before code exists, or compare simulated and real tool execution.

For a real agent or multi-agent system, start with the callable target and `target.trace` instead. The flagship path is [`../travel_planner_langgraph/`](../travel_planner_langgraph/), where OpenTelemetry traces let the judge see tool calls, routing, and intermediate decisions.

## Scenario

The scenario is a health assistant evaluated for **harmful medical advice**. The assistant helps with wellness questions, medication information, and appointment scheduling, but must not provide diagnoses, dosage instructions, or other actionable medical guidance.

## What's in this directory

| Path | What it is |
|---|---|
| `health_assistant*.yaml` | The five Prompt Agent demo configs — the target-shape showcase described below. |
| `agent.py` | The same health assistant *materialised* as a Python callable, so it can be evaluated through `target.callable`. Exposes `chat_modelonly`, `chat_simtools`; `chat_gentools` deliberately raises. |
| `evals/<variant>-<risk>/eval_config.yaml` | One ASSERT eval suite per (variant, risk) pair — behaviour taxonomy, test-set generation, target and judge. |
| `Clarity Protocol/` | The Clarity discovery record: `goal/` (problem + requirements), `failures/failures.md` (the risk register), `mailboxes/` (the discovery journal) and `summary.md`. |
| `README.md` | This file. |

Tool definitions live one level up, in [`../agents/`](../agents/), because they are shared with other examples.

## The five demo configs

The five configs exercise different Prompt Agent options around the same failure mode:

| Config | Target shape | What it demonstrates |
|---|---|---|
| [`health_assistant.yaml`](health_assistant.yaml) | Hosted model + system prompt | Smallest smoke test: no tools, just the model behavior and judge loop. |
| [`health_assistant_simulated_tools.yaml`](health_assistant_simulated_tools.yaml) | Hosted model + fixed toolset + simulator | Tool schemas from [`health_assistant_tools.yaml`](../agents/health_assistant_tools.yaml); an LLM simulator returns tool results. |
| [`health_assistant_sandbox.yaml`](health_assistant_sandbox.yaml) | Hosted model + Python tool module | Real tool functions from [`health_assistant.py`](../agents/health_assistant.py), executed in a Docker-backed sandbox per conversation. |
| [`health_assistant_generated_tools.yaml`](health_assistant_generated_tools.yaml) | Hosted model + per-test-case tools + simulator | Each generated test case carries its own tool definitions; the simulator returns plausible results. |
| [`health_assistant_external.yaml`](health_assistant_external.yaml) | External connector | Advanced/legacy connector path through [`openclaw/`](../agents/openclaw/), with the external agent owning the conversation. |

## Value-add

Prompt Agent evals catch issues while the agent surface is still cheap to change:

- harmful, actionable medical advice that should have been refused or redirected to a clinician
- unsafe use of medication lookup, dosage, or patient-profile results
- missing or ambiguous tool descriptions, arguments, and selection boundaries
- prompt regressions before a real tool backend or orchestration layer exists

> **TDD progression:** “you can test the prompt and toolset design before any agent code is written.” Start with [`health_assistant_simulated_tools.yaml`](health_assistant_simulated_tools.yaml) to iterate on the system prompt + toolset. When the tools are implemented, swap `tools.toolset` + `tools.simulator` for `tools.module` in [`health_assistant_sandbox.yaml`](health_assistant_sandbox.yaml). The eval spec, test generation, and judge stay the same.

Use these demos for Prompt Agent smoke tests, TDD on prompts and toolsets, and simple model-only evals. Do not use them as a substitute for tracing a real agent framework. Once your code owns routing, planning, sub-agents, or tool execution, use [`target.callable` with `target.trace`](../../docs/targets/callable.md). For the full target decision tree, see [`docs/targets/`](../../docs/targets/README.md).

## Environment Variables

| Variable | Required | Purpose |
|---|---|---|
| `AZURE_API_BASE`, `AZURE_API_KEY` | Yes | Azure OpenAI credentials for the agent, the simulator, the generator, and the judge. |

Adjust model names in the YAML if you use a non-Azure [LiteLLM provider](https://docs.litellm.ai/docs/providers).

## How to run the demo configs

From the repo root, install the package and configure your model provider first:

```bash
python -m pip install -e ".[otel]"
cp .env.example .env
# Set AZURE_API_BASE and AZURE_API_KEY.
```

PowerShell equivalent:

```powershell
python -m pip install -e ".[otel]"
Copy-Item .env.example .env
$env:PYTHONIOENCODING = 'utf-8'
```

Run any config with `assert-ai`:

| Config | `assert-ai` |
|---|---|
| Model only | `assert-ai run --config examples/prompt_agents/health_assistant.yaml` |
| Simulated tools | `assert-ai run --config examples/prompt_agents/health_assistant_simulated_tools.yaml` |
| Sandbox tool module | `assert-ai run --config examples/prompt_agents/health_assistant_sandbox.yaml` |
| Generated tools | `assert-ai run --config examples/prompt_agents/health_assistant_generated_tools.yaml` |
| External connector | `assert-ai run --config examples/prompt_agents/health_assistant_external.yaml` |

**Docker prerequisite:** [`health_assistant_sandbox.yaml`](health_assistant_sandbox.yaml) and [`health_assistant_external.yaml`](health_assistant_external.yaml) start containers per conversation. Keep Docker Desktop running before invoking them. The sandbox variant may pull `python:3.11-bookworm`; the external connector builds an OpenClaw image from [`openclaw/Dockerfile`](../agents/openclaw/Dockerfile).

### Files

| File | What it does |
|---|---|
| [`harmful_medical_advice.md`](../behavior_specs/harmful_medical_advice.md) | Eval spec used by the health-assistant configs. |
| [`health_assistant.py`](../agents/health_assistant.py) | Docker-backed tool module: medication lookup, interaction checks, dosage assessment, and patient profile. |
| [`health_assistant_tools.yaml`](../agents/health_assistant_tools.yaml) | Toolset schema for simulator-backed runs. |
| [`openclaw/`](../agents/openclaw/) | Docker assets and connector for the advanced external-connector path. |

### When to use the external-connector path

Use [`../agents/openclaw/`](../agents/openclaw/) only when you need to evaluate an external process that owns the conversation and cannot be represented as a callable. This is the advanced/legacy path. For new customer onboarding, prefer `target.callable` with trace capture; it is simpler, easier to debug, and gives the judge better evidence.

## The measured risks

Clarity discovery ([`Clarity Protocol/`](Clarity%20Protocol/)) surfaced two risks for this
assistant:

| Risk | Failure mode |
|---|---|
| `dosage-guidance-to-elderly-patient` | Gives a personal, actionable dose instruction to a patient who should be referred to a clinician |
| `fabricated-clinical-fact-as-retrieved` | Presents an unverified clinical claim as though it came from a looked-up source |

A Prompt Agent is declared entirely in YAML, so it has no host process for
`target.callable` to point at. `agent.py` therefore **materialises** the same
assistant as a Python callable — it instantiates ASSERT's own `HostedSession`
and `SimulatedResolver` rather than imitating them — so the risks can be measured
per target shape.

### Coverage

| Variant | Risk | Suite |
|---|---|---|
| `simtools` | `dosage-guidance-to-elderly-patient` | `health-assistant-simtools-dosage-guidance-to-elderly-patient` |
| `simtools` | `fabricated-clinical-fact-as-retrieved` | `health-assistant-simtools-fabricated-clinical-fact-as-retrieved` |
| `modelonly` | `dosage-guidance-to-elderly-patient` | `health-assistant-modelonly-dosage-guidance-to-elderly-patient` |
| `modelonly` | `fabricated-clinical-fact-as-retrieved` | Excluded by design — with no tools there is no retrieval claim to make |
| `gentools` | either | Not materialisable, see below |
| `sandbox`, `external` | either | Out of scope (Docker) |

Three suites, not four: `modelonly × fabrication` is excluded rather than
unmeasured. The risk is that a claim is presented *as retrieved*, and a
model-only target retrieves nothing, so there is no such claim to make.

### Why `gentools` is not materialisable

`health_assistant_generated_tools.yaml` sets `test_set.tool_source: per_test_case`, so each
generated test case carries its own tool schemas. In `assert_ai/stages/inference.py`,
`_build_hosted_session` reads those schemas off the test-case row — but `_build_target_session`
constructs a `CallableSession` from `(callable_ref, system_prompt, message_timeout_s,
config_path)` only, and the callable is then invoked as `(message, history=...)`. **The
per-test-case tool payload is never passed to a callable target.** There is no supported
channel through which a `target.callable` can receive the tools that define this variant.

Approximating it with a fixed toolset would silently measure the `simtools` variant while
labelling the result `gentools`. `chat_gentools` therefore raises `NotImplementedError`
instead. An honest "not materialisable" is the correct result here.

## Two traps specific to this domain

1. **No ground truth exists.** Every tool result comes from an LLM simulator
   (`simulator: azure/gpt-5.4-mini`). A fidelity-to-tool-output check is therefore
   *inverted*: it would pass exactly when the assistant propagates a fabrication verbatim.
   None is built. The same fact makes provenance uniform — everything is unverified.
2. **The system prompt requires recommending a healthcare professional**, so deferral
   language appears in every compliant answer as well as in every refusal. It is never the
   discriminator, in either direction. The only sound reading is whether the substantive
   question was answered *alongside* the deferral.

**Availability is a safety property here.** The assistant exists so that elderly patients
keep asking it health questions. An assistant that hedges everything scores zero harm and
closes that channel, and the metric cannot see it, because a patient who stops asking
generates no violation. That is why over-refusal is read next to harm, never after it.

## How to run the evals

```powershell
$env:PYTHONIOENCODING = 'utf-8'   # the CLI crashes on a unicode arrow without this

python -m examples.prompt_agents.agent   # materialisation smoke test

assert-ai run --config examples/prompt_agents/evals/simtools-dosage-guidance-to-elderly-patient/eval_config.yaml
assert-ai run --config examples/prompt_agents/evals/simtools-fabricated-clinical-fact-as-retrieved/eval_config.yaml
assert-ai run --config examples/prompt_agents/evals/modelonly-dosage-guidance-to-elderly-patient/eval_config.yaml
```

## What you should see

Each suite writes to `artifacts/results/<suite>/`, with the suite-level files at the top
and the run files under `baseline/`:

| File | What it holds |
|---|---|
| `taxonomy.json` | The behaviours the suite measures |
| `test_set.jsonl` | The generated test cases |
| `suite.json`, `stratification.json`, `systematization.json` | How the suite was built |
| `baseline/inference_set.jsonl` | The conversation per case |
| `baseline/scores.jsonl` | Per-case judge verdicts and justifications |
| `baseline/metrics.json` | Aggregate rates |
| `baseline/config.yaml`, `baseline/manifest.json` | Exactly what was run |

Read `not_permissible_policy_violation_rate` (harm) and
`permissible_policy_violation_rate` (over-restriction) on **both** `prompt_metrics` and
`scenario_metrics`. There is no pooled suite-level number, and the raw `policy_violation`
rate ORs over all nodes, so it should never headline a comparison.

`assert-ai results status <suite> baseline --json` prints the same numbers from the CLI.

## Notes

- Re-running with an existing `run:` id silently resumes from cache and returns
  byte-identical metrics in under a second. Bump `run:` for a genuinely fresh run.
- `artifacts/` is gitignored, so runs stay local and are never committed.
