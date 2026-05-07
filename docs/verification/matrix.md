# Verification Matrix - v0

> See [`README.md`](README.md) for schema, status definitions, ID convention, and the framework-vs-scenario row distinction.

Last updated: 2026-05-07.

Framework rows seeded from the implementation surface in `p2m/` (pipeline stages, CLI commands, target shapes), Aaron Aspinwall's architecture design doc (2026-04-30), and the Adaptive Eval Production Spec (Chang Liu, May 2026). Scenario rows grounded in Chang's P0 scope (May 7 Teams chat): two agent scenarios (`P2M-AGENT-HEALTH`, `P2M-AGENT-TRAVEL`) probed across framework / endpoint breadth and seed-count scalability tiers.

## 1. Framework Claims

Claims about adaptive-eval itself. Verified by running the pipeline against fixtures and asserting artifact shape, content, CLI behavior, or by scanning the source tree for the expected code surface.

### 1.1 Pipeline stages and artifacts

| spec_id | claim | status | evidence | owner | target_date |
|---|---|---|---|---|---|
| AE-PIPE-S1 | Stage 1 Policy expands a concept markdown into a structured behavior taxonomy with unique behavior IDs and permissibility labels (`policy.json`) | gap-build | - | jakepresent | 2026-05-15 |
| AE-PIPE-S2 | Stage 2 Seeds generates single-turn prompts and multi-turn scenarios stratified across factor levels, each tagged with target behavior and factor combination (`seeds.jsonl`) | gap-build | - | jakepresent | 2026-05-15 |
| AE-PIPE-S3 | Stage 3 Rollout executes test cases against the configured target and records conversation events (`transcripts.jsonl`); when OTel tracing is configured, captures full execution traces | gap-build | - | jakepresent | 2026-05-15 |
| AE-PIPE-S4 | Stage 4 Judge produces a binary verdict per conversation with structured reasoning and per-node judgments, and evidence citations from traces (`scores.jsonl`) | gap-build | - | jakepresent | 2026-05-15 |
| AE-PIPE-S5 | Stage 5 Metrics aggregates scores into dataset-level event rates, per-node conditional rates, and judge failure tracking (`metrics.json`) | gap-build | - | jakepresent | 2026-05-15 |
| AE-PIPE-ART | Suite-level artifacts (`policy.json`, `seeds.jsonl`) are shared across runs; run-level artifacts (`transcripts.jsonl`, `scores.jsonl`, `metrics.json`) are isolated by run ID; no database required | gap-build | - | jakepresent | 2026-05-15 |

### 1.2 CLI surface

| spec_id | claim | status | evidence | owner | target_date |
|---|---|---|---|---|---|
| AE-CLI-RUN | `p2m run --config <yaml>` executes the full pipeline end-to-end and writes all artifacts under `artifacts/results/<suite>/<run>/` | gap-build | - | jakepresent | 2026-05-15 |
| AE-CLI-FORCE | `p2m run --force-stage <stage>` re-runs a specific stage and downstream stages, reusing upstream artifacts | gap-build | - | jakepresent | 2026-05-15 |
| AE-CLI-RESULTS | `p2m results list/status/compare` lists suites and runs, shows status and metrics for a run, and compares two runs | gap-build | - | jakepresent | 2026-05-15 |

### 1.3 Target shapes

| spec_id | claim | status | evidence | owner | target_date |
|---|---|---|---|---|---|
| AE-TGT-MODEL | `target.model` invokes a hosted model directly through LiteLLM with no tool support (P0) | gap-build | - | jakepresent | 2026-05-22 |
| AE-TGT-CALLABLE | `target.callable` accepts a Python function with signature `fn(str) -> str \| ModelResponse` (P0) | gap-build | - | jakepresent | 2026-05-22 |
| AE-TGT-HTTP | `target.endpoint` POSTs each prompt to a user-supplied URL and ingests the response (P0) | gap-build | - | jakepresent | 2026-05-22 |
| AE-TGT-OTEL-PHX | Phoenix/OpenInference auto-instrumentation (`from phoenix.otel import register; register(auto_instrument=True)`) feeds OTel traces to the judge for the **14 Azure-routable supported frameworks** shipped as runnable demos: `autogen`, `crewai`, `dspy`, `haystack`, `instructor`, `langchain`, `langgraph`, `litellm`, `llamaindex`, `openai`, `openai_agents`, `openai_router`, `pydantic_ai`, `smolagents` (P0; LangGraph as the verified anchor) | gap-build | `examples/phoenix_auto_trace/travel_*.py` + `eval_*.yaml`; CI evidence will land via #28 once that merges | jakepresent | 2026-05-22 |
| AE-TGT-OTEL-CUSTOM | Custom OTel SDK instrumentation - manual `tracer.start_as_current_span(...)` spans following [OpenInference semantic conventions](https://arize-ai.github.io/openinference/) - is captured by the same `target.trace` block; verified end-to-end against the multi-agent NeurOSan-style example (P0) | gap-build | `examples/travel_planner_neurosan/agent.py` + `eval_config.yaml` | jakepresent | 2026-05-22 |

### 1.4 Cross-cutting

| spec_id | claim | status | evidence | owner | target_date |
|---|---|---|---|---|---|
| AE-X-LITELLM | All LLM calls (auditor, target-when-model, judge) route through LiteLLM and support Azure / OpenAI / Anthropic providers without code changes | gap-build | - | jakepresent | 2026-05-22 |

## 2. Scenario Coverage - P2M P0

Two agent scenarios (`P2M-AGENT-HEALTH`, `P2M-AGENT-TRAVEL`) probed across framework / endpoint breadth and seed-count scalability tiers. Source: Chang's P0 scoping (Teams chat, May 7) plus the example matrix in PR #28.

### 2.1 Per-agent behavioral correctness

| spec_id | claim | status | evidence | owner | target_date |
|---|---|---|---|---|---|
| P2M-AGENT-HEALTH | P2M correctly evaluates the health-assistant agent: judge verdicts on a known-good vs known-bad seed split match expectations within tolerance | gap-build | `examples/pipes/health_assistant.yaml` | jakepresent | 2026-05-22 |
| P2M-AGENT-TRAVEL | P2M correctly evaluates the travel-planner agent: judge verdicts on a known-good vs known-bad seed split match expectations within tolerance | gap-build | `examples/travel_planner_langgraph` | jakepresent | 2026-05-22 |

### 2.2 Framework / endpoint breadth

| spec_id | claim | status | evidence | owner | target_date |
|---|---|---|---|---|---|
| P2M-FW-LANGGRAPH | travel-planner runs end-to-end against a LangGraph-based target via OTel auto-instrumentation | gap-build | `examples/travel_planner_langgraph/eval_config.yaml` | jakepresent | 2026-05-22 |
| P2M-FW-NEUROSAN | travel-planner runs end-to-end against a NeurOSan-based target | gap-build | `examples/travel_planner_neurosan/eval_config.yaml` | jakepresent | 2026-05-22 |
| P2M-FW-PHOENIX-MULTINODE | Multi-node Phoenix-traced agent runs end-to-end and span collection produces a non-empty trace | gap-build | `examples/phoenix_auto_trace/eval_config.yaml` | jakepresent | 2026-05-22 |
| P2M-FW-PHOENIX-OPENAI | OpenAI-instrumented agent runs end-to-end via Phoenix auto-trace | gap-build | `examples/phoenix_auto_trace/eval_openai.yaml` | jakepresent | 2026-05-22 |
| P2M-FW-PHOENIX-LITELLM | LiteLLM-instrumented agent runs end-to-end via Phoenix auto-trace | gap-build | `examples/phoenix_auto_trace/eval_litellm.yaml` | jakepresent | 2026-05-22 |
| P2M-FW-PHOENIX-LANGCHAIN | LangChain-instrumented agent runs end-to-end via Phoenix auto-trace | gap-build | `examples/phoenix_auto_trace/eval_langchain.yaml` | jakepresent | 2026-05-22 |
| P2M-FW-PHOENIX-DSPY | DSPy-instrumented agent runs end-to-end via Phoenix auto-trace | gap-build | `examples/phoenix_auto_trace/eval_dspy.yaml` | jakepresent | 2026-05-22 |
| P2M-FW-PHOENIX-CREWAI | CrewAI-instrumented agent runs end-to-end via Phoenix auto-trace | gap-build | `examples/phoenix_auto_trace/eval_crewai.yaml` | jakepresent | 2026-05-22 |
| P2M-FW-PIPES-SIMULATED | health-assistant runs end-to-end with simulated tools (`target.tools.simulator` path) | gap-build | `examples/pipes/health_assistant_simulated.yaml` | jakepresent | 2026-05-22 |

### 2.3 Scalability

| spec_id | claim | status | evidence | owner | target_date |
|---|---|---|---|---|---|
| P2M-SCALE-100 | Pipeline completes end-to-end at 100 seeds without failure and within wall-clock budget | gap-build | - | jakepresent | 2026-05-22 |
| P2M-SCALE-1K | Pipeline completes end-to-end at 1,000 seeds without failure and within wall-clock budget | gap-build | - | jakepresent | 2026-05-29 |
| P2M-SCALE-10K | Pipeline completes end-to-end at 10,000 seeds without failure and within wall-clock budget; ties to load-test funding gap (Mohamed May 6) | gap-build | - | jakepresent | 2026-05-29 |

## Notes for v0 → v1

- `gap-build` is the default seed status because no regression has run yet; rows are claims-on-paper, not verified-in-system unless a verifier in `verify.py` says otherwise.
- `evidence` column points at the source config or example today; populates with regression-run links as the harness lands and PRs reference the matrix.
- Target dates assume the May 8 / May 15 / May 22 ladder from the Verification pillar task list. Re-baseline if the ladder shifts.
- Scenario rows for additional reference scenarios (beyond the P0 set) get their own prefixes registered in `README.md` as Chang's on-every-release scenario set lands.
