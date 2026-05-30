# Prompt Templates

These internal-facing prompt templates control what the pipeline tells each model. Most users never need to edit them. The table below maps each file to its pipeline stage.

## Vocabulary note

The pipeline uses two related but distinct judgment layers:

- **permissible / not permissible** — policy labels in the `taxonomy.json` file indicating whether the target model producing this behavior pattern is acceptable (permissible) or undesirable (not permissible). Appears in taxonomy configs, systematization outputs, and the `permissible` field on the generated test sets.
- **violated** — per-node binary judgment the judge assigns after scoring a inference conversation. The judge evaluates each behavior node as `violated = true/false/null` pased on the policy labels.

Prompts and code use these terms consistently; do not interchange them.

## Core Prompts

| File | Stage | Role | Purpose |
|---|---|---|---|
| `systematize_system.md` | `taxonomy` | system | Generates the behavior taxonomy (behavior_categories, definitions, examples) |
| `test_set_direct_single.md` | `test_set` | single | Direct test-case generation (single-turn user messages) |
| `test_set_scenario_single.md` | `test_set` | single | Scenario test-case generation (tester-facing multi-turn briefs) |
| `inference_tester_system.md` | `inference` | system | Persona for the model that plays the user in scenario conversations |
| `judge_system.md` | `judge` | system | Scoring system prompt (per-node violated judgment) |
| `inference_toolsim_user.md` | `inference` | user | Generates simulated tool results for simulated-tools mode |

## Systematization Prompts

Used by the one-step systematization path. `systematization` writes a single `systematization.json` artifact, and `systematization_convert` turns that artifact into `taxonomy.json`.

| File | Stage |
|---|---|
| `systematization_single.md` | `systematization` |
| `systematization_convert_single.md` | `systematization_convert` |
