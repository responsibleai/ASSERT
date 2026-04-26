# Copilot Instructions — Adaptive Eval (P2M)

## Testing

Four test tiers. When asked to "test", run Tier 2. When asked for "small test", run Tier 3.

### Tier 1: Static (`uv run pytest -q`)

Unit tests in `tests/`. No LLM calls, no API keys. Config validation, transcript schemas, OTel span conversion, CLI parsing. Run before every commit.

```bash
uv run pytest -q                                    # full suite (<30s)
uv run pytest tests/test_framework_agnostic.py -q   # OTel-specific
```

### Tier 2: "test" — single demo integration

Run LangGraph demo end-to-end with 2 seeds + `gpt-5.4-mini` judge. Verifies no runtime errors, traces captured, judge produces verdicts.

**Config:** `sub_risk_count: 5`, `prompt.budget: 1`, `scenario.budget: 1`, `max_turns: 4`
**Target:** `examples.travel_planner_langgraph.auto_trace:chat_sync`
**Pass criteria:** 0 judge failures, `trace_events > 0`, `tools_called` non-empty
**Latency:** 30s–90s

### Tier 3: "small test" — two demos, 20 seeds

Run LangGraph + NeurOSan with 20 seeds each (10 prompt + 10 scenario) + `gpt-5.4` judge. Validates both auto + custom instrumentation, failure mode coverage.

**Config:** `sub_risk_count: 5`, `prompt.budget: 10`, `scenario.budget: 10`, `max_turns: 6`
**Pass criteria:** 0 judge failures, ≥3 failure modes per demo, 5/5 sub-risks triggered
**Latency:** 10–15 min

### Tier 4: "bulk test" — all frameworks, 100 seeds

Run all 8 Azure-compatible frameworks (OpenAI, LiteLLM, LangChain, LangGraph, DSPy, CrewAI, LangGraph multi-node, NeurOSan) with 100 seeds each + `gpt-5.4` judge. Overnight job.

**Config:** `sub_risk_count: 10`, `prompt.budget: 20`, `scenario.budget: 80`, `max_turns: 8`, `concurrency: 3`
**Pass criteria:** <5% judge failure rate, ≥8 failure modes per framework
**Latency:** 1–4 hours

## Regression Metrics

Two categories of metrics gate PR quality. If science efficacy metrics regress, both prompt and code changes may be needed (scientists should review). If engineering quality metrics regress, code improvements are needed.

### Pipeline Science Efficacy (canonical)

Defined in `p2m_comparison/` metric scripts. These measure whether the pipeline produces scientifically valid evaluations:

- **Construct Coverage** — % of risk-ontology labels appearing ≥1× in the generated test suite
- **Separation Strength** — average weighted gap in violation rates between model pairs
- **Signal Rate** — % of tests that produce a meaningful eval signal (not noise)
- **Failure Variety** — average number of distinct failure types discovered
- **Item Saturation** — redundancy per test (lower = less redundant)
- **Discrimination Power** — ability to distinguish compliant from non-compliant behavior

Auxiliary science metrics (derived from pipeline artifacts):
- `policy_violation_rate`, `overrefusal_rate`, `judge_failure_rate`, `failure_mode_count`

### Pipeline Engineering Quality

These measure whether the pipeline runs reliably and efficiently:

- **Stage wall times** — per-stage latency (policy, seeds, rollout, judge)
- **Step failure counts** — seed generation failures, rollout timeouts, judge parse errors
- **Unexpected/uncaught errors** — unhandled exceptions, crashes, stack traces
- **Handled-but-unelegant issues** — unclear error messages, confusing output, UX papercuts

### Golden Risk Specs for Regression Testing

Two frozen risk specs cover both safety and quality evaluation:

- `tests/regression/risks/travel_planner_safety.md` — safety-focused: harmful travel advice, unsafe destinations, ignoring advisories (applied to travel planner demo)
- `tests/regression/risks/travel_planner_quality.md` — quality-focused: wrong tool calls, fabricated details, constraint violations, incoherence (applied to travel planner demo)

### PR Regression Test (GitHub Action CI)

On every PR that touches `p2m/` or `prompts/`:

1. **Tier 1** — unit tests (always, <30s)
2. **Tier 4 regression** — full pipeline on both risk specs, freeze risk spec only, let policy→seeds→rollout→judge all run. Default: 50 seeds (PR author can override via label `seeds:20` or `seeds:100`)
3. **Statistical comparison** — paired t-test (same seeds, two commits) on science efficacy metrics. No external dependencies — implement t-test inline using `scipy.stats.ttest_rel`
4. **Gate decision:**
   - **Block PR** if any science efficacy metric shows Degraded + p < 0.05
   - **Warn** if Inconclusive (p > 0.05 but negative trend) or <10 samples
   - **Pass** if Improved or no significant change

## Commands

```bash
# Setup
uv venv && uv sync

# Run the canonical evaluation pipeline
uv run p2m run --config examples/pipes/health_assistant.yaml

# Tests
uv run pytest -q                        # full suite
uv run pytest tests/test_cli.py -q      # single file
uv run pytest tests/test_cli.py::CliTest::test_removed_commands_are_unavailable -q  # single test

# Lint & format
ruff check --fix && ruff format
pyright                                  # type checking

# Viewer (SvelteKit results browser)
cd viewer && npm install && npm run dev  # dev server
cd viewer && npm run check && npm run build  # type-check + build
```

## Architecture

Four-stage YAML-driven evaluation pipeline: **policy → seeds → rollout → judge**.

```
CLI (p2m/cli.py) → config.py → runner.py → stages/{policy,seeds,rollout,judge}.py
```

- **policy** — LLM expands a risk definition (markdown in `examples/risks/`) into a structured taxonomy (`policy.json`).
- **seeds** — LLM generates test cases (prompts + multi-turn scenarios) from the taxonomy → `seeds.jsonl`.
- **rollout** — Executes seeds against the target (hosted model, tool-using agent, or external connector) → `transcripts.jsonl`.
- **judge** — LLM scores each conversation against the policy → `scores.jsonl` + `metrics.json`.

Artifacts land in `artifacts/results/<suite>/<run>/`. The viewer reads them directly from disk — no database.

### Key modules

| Module | Role |
|---|---|
| `p2m/config.py` + `p2m/core/config_model.py` | YAML loading, validation, typed dataclasses |
| `p2m/runner.py` | Stage orchestration, manifest management |
| `p2m/core/session.py` | `HostedSession`, `CallableSession`, `HTTPEndpointSession`, `ExternalSession` |
| `p2m/core/model_client.py` | LiteLLM-backed model calls, normalized tool-call types |
| `p2m/core/transcript.py` | Event-based transcript model shared by rollout, judge, and viewer |
| `p2m/core/judge.py` | Verdict aggregation, citation resolution |

### Target shapes (rollout)

The `pipeline.rollout.target` block determines execution mode:

- Plain hosted model: `target.model.name`
- Hosted + Python tools: `target.model` + `target.tools.module`
- Hosted + simulated tools: `target.model` + `target.tools.toolset` + `target.tools.simulator`
- External agent: `target.connector`

### Systematization stages

Optional stages (`systematization`, `systematization_convert`) produce `systematization.json` → `policy.json`. These are separate from the standard 4-stage pipeline.

## Security

- Never push to any git remote other than `origin` without explicit user confirmation of the remote name.
- Never create public repositories or change repository visibility settings.
- Never commit `.env`, credentials, or API keys.
- The remote `PRIVATE-NDA-CUSTOMERS` (if configured) is for private preview distribution only. Always confirm before pushing to it.

## Conventions

- **Python 3.11+**. Type hints on public helpers. `snake_case` everywhere in Python/YAML. `PascalCase` in Svelte.
- **Conventional Commits**: `fix:`, `feat:`, `refactor:`, `docs:`.
- **No hardcoding** unless necessary. No fallbacks unless explicitly requested. No unnecessary abstractions.
- **Model names** use LiteLLM's `provider/model-name` format (e.g., `azure/gpt-5.4`, `openai/gpt-4o`). Credentials come from env vars or `.env`.
- **`model`** in YAML config is always a mapping (`name`, `temperature`, `max_tokens`), never a bare string.
- **Config validation**: `max_turns` lives on `pipeline.rollout`, not inside `auditor`.
- **Tests**: pytest with `unittest.TestCase` classes. Name tests after behavior. Focus on pipeline I/O, artifact schemas, judgment aggregation. Run `uv run pytest -q` before PRs.
- **Cleanup on every touched file**: inline single-use helpers, remove dead code/config keys/wrappers/fallbacks. One parsing path, one prompt path, one config path. Constants at module top.
- **Docs**: two audiences (PM: what it does / contributor: how to change). Code examples must be real and runnable. Update `docs/architecture-map.md` if control flow changes.

## Terminology

Use new terminology — do not frame adaptive evaluation as safety-only:

| Old term | Current term |
|---|---|
| Risk | Failure Modes |
| Risk Definitions | Failure Mode Definitions |
| Suite | Dataset |
| Pipeline | Generation |
| Taxonomy | Requirement Map |
| Seeds | Test Cases |
| Audit | Conversations |
