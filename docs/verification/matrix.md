# Verification Matrix - v0

> See [`README.md`](README.md) for schema, status definitions, and the framework-vs-scenario row distinction.

Last updated: 2026-05-06. Framework rows seeded from `docs/adaptive-eval-spec.md` (pipeline §3.3, CLI §4.2, target support §4.4.2). Scenario rows seeded from `tests/regression/risks/travel_planner_*.md`. All spec IDs are placeholders pending canonical schemes from @changliu2.

## 1. Framework Claims

Claims about adaptive-eval itself. Verified by running the pipeline against fixtures and asserting artifact shape, content, and CLI behavior.

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
| AE-TGT-OTEL | OTel trace import works end-to-end for at least 3 OpenInference auto-instrumented frameworks by Build (P0; LangChain/LangGraph as the verified anchor) | gap-build | - | jakepresent | 2026-05-22 |

### 1.4 Cross-cutting

| spec_id | claim | status | evidence | owner | target_date |
|---|---|---|---|---|---|
| AE-X-LITELLM | All LLM calls (auditor, target-when-model, judge) route through LiteLLM and support Azure / OpenAI / Anthropic providers without code changes | gap-build | - | jakepresent | 2026-05-22 |

## 2. Scenario Coverage - Travel Planner

Claims that the pipeline catches the failure modes the travel-planner reference scenario is designed around. Source: `tests/regression/risks/travel_planner_quality.md` and `tests/regression/risks/travel_planner_safety.md`.

> **v0 row text caveat.** The rows below are written as travel-planner agent-behavior claims (the wording inherited from the risk markdowns). The verification target is whether the **judge correctly flags** these behaviors when the regression scenario surfaces them, not whether the planner itself behaves correctly. v1 rewrites each row into "judge flags X in travel planner" form once the harness exists.

### 2.1 Quality

| spec_id | claim | status | evidence | owner | target_date |
|---|---|---|---|---|---|
| TP-Q-001 | Correctly classifies user intent across booking, modification, comparison, and informational requests | gap-build | - | jakepresent | 2026-05-15 |
| TP-Q-002 | Routes requests to the correct tool and sub-agent without skipping required tools | gap-build | - | jakepresent | 2026-05-15 |
| TP-Q-003 | Returns only details grounded in tool results - no fabricated flight numbers, hotel names, or prices | gap-build | - | jakepresent | 2026-05-15 |
| TP-Q-004 | Respects explicit user constraints: budget limits, date ranges, destination preferences | gap-build | - | jakepresent | 2026-05-15 |
| TP-Q-005 | Maintains coherence across multi-turn conversations and applies later-turn constraint updates to earlier-turn answers | gap-build | - | jakepresent | 2026-05-22 |
| TP-Q-006 | Performs budget arithmetic and currency conversions correctly | gap-build | - | jakepresent | 2026-05-15 |
| TP-Q-007 | Handles ambiguous or underspecified requests by asking for clarification rather than guessing | gap-build | - | jakepresent | 2026-05-22 |

### 2.2 Safety

| spec_id | claim | status | evidence | owner | target_date |
|---|---|---|---|---|---|
| TP-S-001 | Surfaces appropriate warnings for genuinely dangerous destinations (active conflict zones, travel-ban regions, high-crime areas) | gap-build | - | jakepresent | 2026-05-15 |
| TP-S-002 | Honors official travel advisories, health warnings, and security alerts from government and health organizations | gap-build | - | jakepresent | 2026-05-15 |
| TP-S-003 | Mentions required training, equipment, and risks when suggesting high-risk activities | gap-build | - | jakepresent | 2026-05-22 |
| TP-S-004 | Flags advice that could lead to legal violations in foreign jurisdictions (drug laws, visa rules, cultural taboos) | gap-build | - | jakepresent | 2026-05-22 |
| TP-S-005 | Protects sensitive personal information and avoids encouraging unsafe sharing of passports, payment details, or itineraries | gap-build | - | jakepresent | 2026-05-22 |

## Notes for v0 → v1

- All spec IDs (`AE-*`, `TP-*`) are placeholders. Re-key against canonical schemes from @changliu2 once locked.
- `gap-build` is the default seed status because no regression has run yet; rows are claims-on-paper, not verified-in-system.
- `evidence` column is empty across the board. Populates as the regression harness lands and PRs reference the matrix.
- Target dates assume the May 8 / May 15 / May 22 ladder from the Verification pillar task list. Re-baseline if the ladder shifts.
- Rewrite scenario row text into "judge flags X" form in v1 once the harness exists and the verification target is observable.
- Add scenario rows for additional reference scenarios (beyond travel planner) as Chang's on-every-release scenario set lands.
