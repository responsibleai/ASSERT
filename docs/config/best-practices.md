# Config Best Practices and Limitations

## Best practices

- Write behavior specs around real product failure modes, not generic benchmark criteria.
- Keep `context` concrete about tools, users, and constraints.
- Add `stratify.dimensions` when coverage across user types or scenarios matters.
- Prefer `target.callable` with `target.trace` for agents so judges can inspect process evidence.
- Keep judge rubrics binary and evidence-oriented (`true`/`false` conditions).
- Re-run from the earliest changed stage using `--force-stage`.
- Version suites intentionally and keep run IDs meaningful.

## Recommended target strategy

1. Any Python agent or multi-agent system: use `target.callable` + `target.trace`.
2. Prompt and tool-schema workflows: use `target.model` + `target.tools`.
3. Plain callable without traces: use only as fallback for black-box targets.

## Common pitfalls

- Vague `behavior.description` produces shallow categories and weak tests.
- Missing credentials lead to silent provider failures during inference/judge.
- Mixing prompt and scenario goals without dimension planning reduces coverage quality.
- Reusing stale artifacts without `--force-stage` can hide config changes.

## Current limitations

- Viewer is read-only and does not create evals or launch runs.
- Non-instrumented targets limit what the judge can verify.
- Some advanced schema behavior is evolving in preview; check `docs/config/schema.md` for the source-of-truth details.

## Checklist before running

- `.env` configured for your chosen model provider
- target import path resolves and is executable
- judge dimensions and rubrics are explicit
- sample sizes are feasible for budget and runtime
- desired trace backend configured when using OTel
