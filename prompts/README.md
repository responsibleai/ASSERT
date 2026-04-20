# Prompt Templates

These templates control what the pipeline tells each model. Most users never need to edit them. The table below maps each file to its pipeline stage.

## Vocabulary note

The pipeline uses two related but distinct judgment layers:

- **permissible / not permissible** — policy-level label on each behavior indicating whether the target model producing this behavior pattern is acceptable (permissible) or undesirable (not permissible). Appears in policy configs, systematization outputs, and the `permissible` field on seeds.
- **violated** — per-node binary judgment the judge assigns after scoring a rollout conversation. The judge evaluates each behavior node as `violated = true/false/null`.

Prompts and code use these terms consistently; do not interchange them.

## Core Prompts

| File | Stage | Role | Purpose |
|---|---|---|---|
| `policy_system.md` | `policy` | system | Generates the concept taxonomy (behaviors, definitions, examples) |
| `seeds_direct_single.md` | `seeds` | single | Direct seed generation (single-turn user messages) |
| `seeds_scenario_single.md` | `seeds` | single | Scenario seed generation (auditor-facing multi-turn briefs) |
| `rollout_auditor_system.md` | `rollout` | system | Persona for the model that plays the user in scenario conversations |
| `judge_system.md` | `judge` | system | Scoring system prompt (per-node violated judgment) |
| `rollout_toolsim_user.md` | `rollout` | user | Generates simulated tool results for simulated-tools mode |

## Systematization Prompts

Used by the one-step systematization path. `systematization` writes a single `systematization.json` artifact, and `systematization_convert` turns that artifact into `policy.json`.

| File | Stage |
|---|---|
| `systematization_single.md` | `systematization` |
| `systematization_convert_single.md` | `systematization_convert` |
