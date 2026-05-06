# Prompt Templates

These templates control what the pipeline tells each model. Most users never need to edit them. The table below maps each file to its pipeline stage.

## Vocabulary note

The pipeline uses two related but distinct judgment layers:

- **permissible / not permissible** — taxonomy-level label on each failure_mode indicating whether the target model producing this failure_mode pattern is acceptable (permissible) or undesirable (not permissible). Appears in taxonomy configs, systematization outputs, and the `permissible` field on seeds.
- **violated** — per-node binary judgment the judge assigns after scoring a inference conversation. The judge evaluates each failure_mode node as `violated = true/false/null`.

Prompts and code use these terms consistently; do not interchange them.

## Core Prompts

| File | Stage | Role | Purpose |
|---|---|---|---|
| `taxonomy_system.md` | `taxonomy` | system | Generates the spec taxonomy (failure_modes, definitions, examples) |
| `seeds_direct_single.md` | `seeds` | single | Direct seed generation (single-turn user messages) |
| `seeds_scenario_single.md` | `seeds` | single | Scenario seed generation (tester-facing multi-turn briefs) |
| `inference_tester_system.md` | `inference` | system | Persona for the model that plays the user in scenario conversations |
| `judge_system.md` | `judge` | system | Scoring system prompt (per-node violated judgment) |
| `inference_toolsim_user.md` | `inference` | user | Generates simulated tool results for simulated-tools mode |

## Systematization Prompts

Used by the one-step systematization path. `systematization` writes a single `systematization.json` artifact, and `systematization_convert` turns that artifact into `taxonomy.json`.

| File | Stage |
|---|---|
| `systematization_single.md` | `systematization` |
| `systematization_convert_single.md` | `systematization_convert` |
