# ASSERT

**ASSERT** stands for **Adaptive Spec-driven Scoring for Evaluation and Regression Testing**.

ASSERT is a local-first, framework-agnostic, trace-aware evaluation harness for agents and multi-agent systems.

> **Customer preview.** ASSERT is a preview / POC distribution for design partners. The core workflow is stable: write an eval spec, generate targeted test cases, execute them against your agent, and judge the results against your rubric. Some YAML field names are still evolving; the docs bridge current names to the intended developer-facing terminology.

## Why ASSERT

Most eval tools start with a fixed benchmark. Real agents fail in product-specific ways: they call the wrong tool, ignore a constraint, fabricate a price, skip a safety check, or agree with a risky plan.

ASSERT flips the workflow. **You write a short spec describing what your agent should and should not do.** The pipeline derives behavior categories, generates single-turn and multi-turn test cases, executes them against your target, and uses an LLM judge to score each conversation against your spec. **Any agent or multi-agent system** that runs in Python plugs in through `target.callable`. The recommended integration captures the agent's OpenTelemetry spans (Phoenix/OpenInference auto-instruments 33+ frameworks in two lines, or you can emit your own with the OTel SDK) so the judge can inspect tool calls, arguments, routing, latency, and intermediate decisions — not just the final response.

You get:

- **Spec-driven coverage** - test cases are generated from your product requirements, not a generic benchmark.
- **Any agent works** - evaluate a LangGraph agent, a CrewAI / OpenAI Agents SDK / DSPy / LlamaIndex / AutoGen system, custom multi-agent orchestration, a Python callable, or a hosted model — without rewriting the eval pipeline.
- **Trace-grounded judgment** - the recommended integration captures OpenTelemetry spans (Phoenix/OpenInference auto-instruments 33+ frameworks in two lines, or you can emit your own with the OTel SDK) so the judge can cite tool calls, routing, model calls, and latency as evidence — not just the final response.
- **Portable artifacts** - every stage writes JSON/JSONL files locally for inspection, CI, and sharing.

## Quickstart: LangGraph travel planner (any agent works the same way)

The flagship example evaluates a multi-tool LangGraph travel planner. The target is reached through `target.callable` — the same integration boundary you would use for any agent or multi-agent system — and Phoenix/OpenInference auto-instrumentation captures the agent's OpenTelemetry spans so the judge can cite tool calls and routing decisions. **This is the recommended integration shape for any non-trivial agent.**

Recommended install path for preview customers:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[otel,langgraph]"
cp .env.example .env
# Edit .env with credentials for your provider. Defaults match the example's `azure/...` model;
# any LiteLLM provider (OpenAI, Anthropic, Bedrock, Vertex, Ollama, …) works — see https://docs.litellm.ai/docs/providers.

# Optional: run Phoenix locally if you want to browse traces.
phoenix serve

# Run the full pipeline: spec -> taxonomy -> test cases -> execution -> verdicts.
assert-eval run --config examples/travel_planner_langgraph/eval_config.yaml

# Inspect the run.
assert-eval results status travel-planner-langgraph-v1 demo-1
```

Codespaces / VS Code Dev Containers:

[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/microsoft/ASSERT)

The repo includes a minimal dev container for the LangGraph quickstart. It installs `.[otel,langgraph,dev]`, copies `.env.example` to `.env` if needed, and forwards Phoenix on port `6006`. After the container finishes setup, add your provider credentials to `.env` and run the same `assert-eval run` command above.

Windows PowerShell equivalent:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[otel,langgraph]"
Copy-Item .env.example .env

phoenix serve
assert-eval run --config examples/travel_planner_langgraph/eval_config.yaml
assert-eval results status travel-planner-langgraph-v1 demo-1
```

What the quickstart does:

| Step | Developer behavior | Current YAML / artifact |
|---|---|---|
| 1 | **Eval spec**: plain-English behavior requirements | `behavior.name` and `behavior.description` live inline in `eval_config.yaml` |
| 2 | **Behavior categories**: generated failure-mode taxonomy | `pipeline.systematize` writes `taxonomy.json` |
| 3 | **Test cases**: prompts and multi-turn scenarios | `pipeline.test_set` writes `test_set.jsonl` |
| 4 | **Execute**: run the agent and capture traces | `pipeline.inference.target.callable` + `target.trace` write `inference_set.jsonl` |
| 5 | **Judge**: score against your rubric | `pipeline.judge.dimensions` writes `scores.jsonl` and `metrics.json` |

Start with the full walkthrough: [`docs/quickstart.md`](docs/quickstart.md).

### Create your own config with `assert-eval init`

Don't want to write YAML by hand? `assert-eval init` starts a conversational LLM assistant that asks about your agent, eval goals, and constraints, then proposes a complete config.

`assert-eval init` needs an LLM to power the conversation. Pass `--model` with any [LiteLLM model string](https://docs.litellm.ai/docs/providers) and make sure the matching API key is set in your `.env` file (loaded by default) or environment:

```bash
assert-eval init --model azure/gpt-5.4
# or skip the first question:
assert-eval init --model azure/gpt-5.4 --describe "A customer-support chatbot with order-lookup and refund tools"
# or edit/extend an existing config:
assert-eval init --model azure/gpt-5.4 --from examples/travel_planner_langgraph/eval_config.yaml
```

See [`docs/reference/cli.md`](docs/reference/cli.md#design-a-config-interactively) for the full option reference.

## How it works

```text
one eval_config.yaml
        |
        v
behavior categories  ->  test cases + variations  ->  execute target  ->  judge
        |                         |                         |              |
        v                         v                         v              v
   taxonomy.json                test_set.jsonl          inference_set.jsonl   scores.jsonl
                                                     + OTel traces     metrics.json
```

Today the YAML still uses implementation names such as `behavior`, `dimensions`, `taxonomy`, `test_set`, and `inference`. The docs use the developer-facing behaviors - spec, variations, test cases, execute, judge - and call out the current YAML key the first time each behavior appears. See [`docs/concepts.md`](docs/concepts.md) for the bridge.

## Choose your target

Pick a target based on how your agent is built.

| Your target looks like... | Use this path | Start here |
|---|---|---|
| Any agent or multi-agent system you can invoke from Python (LangGraph, CrewAI, OpenAI Agents SDK, DSPy, LlamaIndex, AutoGen / MAF, custom orchestration, …) | **Callable target with OTel traces (recommended)**: point `target.callable` at your entry function and add `target.trace` so Phoenix/OpenInference (or your own OTel SDK spans) feed tool calls, routing, model calls, and latency to the judge | [`docs/targets/callable.md`](docs/targets/callable.md) |
| A system prompt + tool schema, no orchestration code yet | **Prompt Agent target** (`target.model`, `target.system_prompt`, `target.tools`): the runtime owns the tool-call loop (up to 10 rounds, real or simulated tools). Best for test-driven prompt + toolset design before any agent is implemented | [`docs/targets/model-and-tools.md`](docs/targets/model-and-tools.md) |
| A black-box API you cannot instrument | **Plain callable (customization fallback, not recommended)**: `target.callable` with no `target.trace`. The judge sees only the final response — use only when instrumentation is impossible | [`docs/targets/callable.md`](docs/targets/callable.md#customization-without-traces) |

**Use simulated tools intentionally:** simulated tools are helpful for Prompt Agents when real backends are not ready. They are not a substitute for tracing a real multi-agent framework.

## Examples

| Example | What it shows | Best for |
|---|---|---|
| [`examples/travel_planner_langgraph`](examples/travel_planner_langgraph/) | Full pipeline with `target.callable` + Phoenix OTel trace capture, generated test cases, and judge dimensions for quality + safety | Start here for any agent or multi-agent system |
| [`examples/phoenix_auto_trace`](examples/phoenix_auto_trace/) | The same travel-planner idea across 33 framework instrumentation paths | Understanding framework breadth |
| [`examples/pipes/health_assistant.yaml`](examples/pipes/health_assistant.yaml) | Simple hosted model target with a system prompt | Quick smoke test for a single model |
| [`examples/pipes/health_assistant_simulated_tools.yaml`](examples/pipes/health_assistant_simulated_tools.yaml) | Hosted model with simulated tool responses from a fixed schema | Prompt Agents with planned tools but no backend yet |

See [`examples/README.md`](examples/README.md).

## Artifacts

Every run writes a self-contained directory under `artifacts/results/<suite>/<run>/`:

```text
artifacts/results/<suite>/
├── suite.json
├── taxonomy.json
├── test_set.jsonl
└── <run>/
    ├── manifest.json
    ├── config.yaml
    ├── inference_set.jsonl
    ├── scores.jsonl
    └── metrics.json
```

These artifacts are portable and inspectable:

- `taxonomy.json` - generated behavior taxonomy from your spec.
- `test_set.jsonl` - generated prompts and scenarios.
- `inference_set.jsonl` - inference outputs (conversations or agent actions) and trace references.
- `scores.jsonl` - per-inference verdicts with reasoning and evidence.
- `metrics.json` - aggregate rates by judge dimension and behavior category.

Browse them with the CLI, the local viewer, or any JSONL tool. Nothing leaves your machine unless you send it somewhere.

## Documentation map

- **Get started:** [`docs/quickstart.md`](docs/quickstart.md), [`docs/concepts.md`](docs/concepts.md)
- **Targets:** [`docs/targets/`](docs/targets/) (overview), [`docs/targets/callable.md`](docs/targets/callable.md) (any agent), [`docs/targets/model-and-tools.md`](docs/targets/model-and-tools.md)
- **Authoring:** [`docs/writing-eval-specs.md`](docs/writing-eval-specs.md), [`docs/reading-results.md`](docs/reading-results.md)
- **Create a config:** `assert-eval init` — interactive config designer ([`docs/reference/cli.md`](docs/reference/cli.md#design-a-config-interactively))
- **Reference:** [`docs/reference/cli.md`](docs/reference/cli.md), [`CONFIG_REFERENCE.md`](CONFIG_REFERENCE.md)
- **AI assistants:** [`AGENTS.md`](AGENTS.md)
- **Preview status:** [`docs/status-and-roadmap.md`](docs/status-and-roadmap.md)

## Status

ASSERT is a customer preview / POC, not a GA service.

Stable enough to try:

- `assert-eval init` — conversational config designer
- spec -> behavior categories -> test cases -> execute -> judge workflow
- local artifact layout
- `target.callable` with OTel trace capture (Phoenix/OpenInference for 33+ frameworks, or your own OTel SDK spans) — the recommended integration path
- hosted model and Prompt Agent targets

Still evolving:

- public terminology and YAML aliases
- hosted/cloud integration story
- framework-specific quickstarts beyond the current examples

Preview feedback is welcome: confusing names, missing target examples, trace gaps, judge behavior, artifact shape, and docs clarity are all useful signals.

## Troubleshooting

- **macOS, `litellm` AttributeError after install** — some macOS security tooling can silently truncate the `litellm` wheel during extraction with `uv sync`, causing errors like `AttributeError: module 'litellm' has no attribute 'acompletion'`. The `pip install -e ".[otel,langgraph]"` path above uses copy-based installs and avoids this. If you must use `uv`, grant your terminal Full Disk Access and run `xattr -cr .venv` to clear quarantine attributes.
- **Windows, `UnicodeEncodeError` when running auto-trace demos** — set `$env:PYTHONUTF8 = "1"` before `python -m examples.phoenix_auto_trace.travel_openai`.
- **Docker-backed pipes fail with "docker daemon unavailable"** — `examples/pipes/health_assistant_sandbox.yaml` and `_external.yaml` need Docker Desktop running.

## Telemetry

This project does not collect or send telemetry to Microsoft by default. Runs write local artifacts under `artifacts/results/`, and optional OpenTelemetry trace capture is controlled by your configuration and local collector setup, such as Phoenix.

If you configure a target, judge, trace collector, or model provider to send data to an external service, the prompts, responses, traces, metadata, and other evaluation artifacts sent to that service are governed by that service's terms and your configuration.

## Trademarks

This project may contain trademarks or logos for projects, products, or services. Authorized use of Microsoft trademarks or logos is subject to and must follow [Microsoft's Trademark & Brand Guidelines](https://www.microsoft.com/en-us/legal/intellectualproperty/trademarks). Use of Microsoft trademarks or logos in modified versions of this project must not cause confusion or imply Microsoft sponsorship. Any use of third-party trademarks or logos is subject to those third party's policies.

## Important: Risks and limitations

ASSERT is designed to generate and run scenario-based evaluations for AI systems, including adversarial and edge-case tests. These scenarios are intended to help surface potential weaknesses, unsafe behaviors, and other undesirable outcomes. They do not guarantee that a system has failed, nor are they guarantees that a system is safe.

Because generated scenarios can meaningfully affect system behavior, using this product without adequate sandboxing or environment controls can cause real-world side effects. Depending on the target system, evaluations may trigger unwanted actions such as data modification or deletion, information disclosure, code or configuration changes, external messages, or other operational impacts.

You are responsible for ensuring that evaluations run only in environments that are appropriate for testing, including the use of:

- test or synthetic data where possible
- restricted credentials and scoped permissions
- isolated or non-production systems
- safeguards for logging, storage, and external actions

You should review generated adversarial or stress-test prompts before use and confirm that your environment can safely handle them. Some generated scenarios may involve jailbreak-style behavior, prompt injection, tool misuse, over-broad requests, or other forms of adversarial interaction.

ASSERT is not a compliance or certification tool. You and your users remain responsible for ensuring that evaluated systems comply with applicable laws, regulations, contractual obligations, internal policies, and industry standards.

Use of this system may also result in meaningful compute and inference costs. You should monitor usage, model calls, tool execution, and resource consumption during evaluations.

### Additional limitations

- **Real system side effects may occur.** Evaluations can trigger writes, messages, workflow actions, code changes, ticket creation, or other effects if the target is connected to live systems.
- **Results are scenario-dependent.** Outcomes depend on the generated scenario, available tools, retrieved context, system configuration, and runtime environment.
- **Automated judgments are best-effort.** LLM-based scoring and review can be incorrect; treat single-run outputs as signals for investigation, not definitive truth.
- **Run-to-run behavior may vary.** Results may differ across runs, especially for multi-turn or tool-using systems.
- **Untrusted content can affect outcomes.** Retrieved documents, tool outputs, and external content may influence both the target system and automated judges in unexpected ways.
- **Sensitive content may appear in artifacts.** If the evaluated system emits secrets, personal data, or restricted content, that material may appear in logs, traces, prompts, outputs, or evaluation artifacts.
- **Costs may scale quickly.** Large evaluations, repeated retries, or tool-heavy runs can incur substantial inference and execution costs.
- **This is not a substitute for human review.** High-stakes conclusions should be supported by expert review, grounded evidence, and, where appropriate, additional statistical validation.
- **Reproducibility may be imperfect.** Results can vary across model versions, deployments, tool backends, and runtime settings.

