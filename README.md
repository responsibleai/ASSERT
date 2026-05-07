<p align="center">
  <img src="logo.jpg" alt="Adaptive Eval" width="60%">
</p>

# Adaptive Eval

**Spec-driven evaluation for any agent or multi-agent system - local-first, framework-agnostic, and trace-aware.**

> **Customer preview.** Adaptive Eval is a preview / POC distribution for design partners. The core workflow is stable: write an eval spec, generate targeted test cases, execute them against your agent, and judge the results against your rubric. Some YAML field names are still evolving; the docs bridge current names to the intended developer-facing terminology.

## Why Adaptive Eval

Most eval tools start with a fixed benchmark. Real agents fail in product-specific ways: they call the wrong tool, ignore a constraint, fabricate a price, skip a safety check, or agree with a risky plan.

Adaptive Eval flips the workflow. **You write a short spec describing what your agent should and should not do.** The pipeline derives behavior categories, generates single-turn and multi-turn test cases, executes them against your target, and uses an LLM judge to score each conversation against your spec. **Any agent or multi-agent system** that runs in Python plugs in through `target.callable`. The recommended integration captures the agent's OpenTelemetry spans (Phoenix/OpenInference auto-instruments 33+ frameworks in two lines, or you can emit your own with the OTel SDK) so the judge can inspect tool calls, arguments, routing, latency, and intermediate decisions — not just the final response.

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
p2m run --config examples/travel_planner_langgraph/eval_config.yaml

# Inspect the run.
p2m results status travel-planner-langgraph-v1 demo-1
```

Windows PowerShell equivalent:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[otel,langgraph]"
Copy-Item .env.example .env

phoenix serve
p2m run --config examples/travel_planner_langgraph/eval_config.yaml
p2m results status travel-planner-langgraph-v1 demo-1
```

What the quickstart does:

| Step | Developer concept | Current YAML / artifact |
|---|---|---|
| 1 | **Eval spec**: plain-English behavior requirements | `concept.name: travel_planner_eval` loads `examples/travel_planner_langgraph/travel_planner_eval.md` |
| 2 | **Behavior categories**: generated failure-mode taxonomy | `pipeline.policy` writes `policy.json` |
| 3 | **Test cases**: prompts and multi-turn scenarios | `pipeline.seeds` writes `seeds.jsonl` |
| 4 | **Execute**: run the agent and capture traces | `pipeline.rollout.target.callable` + `target.trace` write `transcripts.jsonl` |
| 5 | **Judge**: score against your rubric | `pipeline.judge.dimensions` writes `scores.jsonl` and `metrics.json` |

Start with the full walkthrough: [`docs/quickstart.md`](docs/quickstart.md).

## How it works

```text
your eval spec (.md)
        |
        v
behavior categories  ->  test cases + variations  ->  execute target  ->  judge
        |                         |                         |              |
        v                         v                         v              v
   policy.json                seeds.jsonl          transcripts.jsonl   scores.jsonl
                                                     + OTel traces     metrics.json
```

Today the YAML still uses implementation names such as `concept`, `factors`, `policy`, `seeds`, and `rollout`. The docs use the developer-facing concepts - spec, variations, test cases, execute, judge - and call out the current YAML key the first time each concept appears. See [`docs/concepts.md`](docs/concepts.md) for the bridge.

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
├── policy.json
├── seeds.jsonl
└── <run>/
    ├── manifest.json
    ├── config.yaml
    ├── transcripts.jsonl
    ├── scores.jsonl
    └── metrics.json
```

These artifacts are portable and inspectable:

- `policy.json` - generated behavior taxonomy from your spec.
- `seeds.jsonl` - generated prompts and scenarios.
- `transcripts.jsonl` - target conversations and trace references.
- `scores.jsonl` - per-conversation verdicts with reasoning and evidence.
- `metrics.json` - aggregate rates by judge dimension and behavior category.

Browse them with the CLI, the local viewer, or any JSONL tool. Nothing leaves your machine unless you send it somewhere.

## Documentation map

- **Get started:** [`docs/quickstart.md`](docs/quickstart.md), [`docs/concepts.md`](docs/concepts.md)
- **Targets:** [`docs/targets/`](docs/targets/) (overview), [`docs/targets/callable.md`](docs/targets/callable.md) (any agent), [`docs/targets/model-and-tools.md`](docs/targets/model-and-tools.md)
- **Authoring:** [`docs/writing-eval-specs.md`](docs/writing-eval-specs.md), [`docs/reading-results.md`](docs/reading-results.md)
- **Reference:** [`docs/reference/cli.md`](docs/reference/cli.md), [`CONFIG_REFERENCE.md`](CONFIG_REFERENCE.md)
- **AI assistants:** [`AGENTS.md`](AGENTS.md)
- **Preview status:** [`docs/status-and-roadmap.md`](docs/status-and-roadmap.md)

## Status

Adaptive Eval is a customer preview / POC, not a GA service.

Stable enough to try:

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

