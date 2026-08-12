# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- PyRIT-backed `pipeline.red_team` stage and `assert-ai[redteam]` extra for direct Baseline attacks against ASSERT targets, with native findings, explicit outbound sink evidence, viewer support, and a manual GitHub Actions workflow.
- ACS guardrail adapter (`assert-ai[acs]` extra): turn a completed run's findings into a deployable Agent Control Specification policy via `assert-ai acs generate`, validate it against known-bad examples with `assert-ai acs validate`, and re-run a target guarded with the `guard_target` Python API. See `docs/guides/securing-agents-with-acs.md`.
- `assert-ai acs eval-config`: generate a small ASSERT config from an existing ACS manifest for regression/sanity checking an already-guarded target without requiring ACS runtime dependencies.

### Changed

- Updated the DSPy GEPA extra to the current 3.x package and declared its incompatible uv extra combinations so the committed lock resolves on current PyPI.

### Fixed

- Pinned Phoenix to `15.3.0` so the supported Python 3.11 test environment can import its pytest plugin.

[Unreleased]: https://github.com/responsibleai/ASSERT/commits/main
