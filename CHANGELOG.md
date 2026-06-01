# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- **BREAKING:** Python package renamed `assert_eval` → `assert_ai`. CLI entrypoint
  is now `assert-ai` (previously `assert-eval`). Update your imports and shell
  commands.
- **BREAKING:** Environment variables renamed `ASSERT_EVAL_*` → `ASSERT_AI_*`.
  The pre-rename `P2M_*` aliases are also removed. Update your `.env` files,
  CI secrets, and shell profiles. The viewer and runtime read only the
  `ASSERT_AI_*` names.
- Internal prompt templates moved from `internal-pipeline-prompts/` to
  `assert_ai/internal_pipeline_prompts/` so the package is wheel-importable
  via `importlib.resources`. No user-visible API change unless you were
  reading these files directly.

### Migration

1. Replace `import assert_eval` with `import assert_ai`. Replace
   `assert-eval` CLI invocations with `assert-ai`.
2. Rename env vars: e.g., `ASSERT_EVAL_RUNS_ROOT=...` → `ASSERT_AI_RUNS_ROOT=...`.
3. Reinstall: `pip install -e ".[otel]"` (or your usual extras).

### Added

- PEP 517 wheel-build CI matrix on Linux/macOS/Windows × Python 3.10/3.11/3.12
  to verify package importability across platforms.

[Unreleased]: https://github.com/responsibleai/ASSERT/commits/main
