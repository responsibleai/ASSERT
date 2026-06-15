# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- ACS guardrail adapter (`assert-ai[acs]` extra): turn a completed run's findings into a deployable Agent Control Specification policy via `assert-ai acs generate`, validate it against known-bad examples with `assert-ai acs validate`, and re-run a target guarded with the `guard_target` Python API. See `docs/guides/securing-agents-with-acs.md`.
- `assert-ai acs eval-config`: generate a small ASSERT config from an existing ACS manifest for regression/sanity checking an already-guarded target without requiring ACS runtime dependencies.

### Changed

### Fixed

[Unreleased]: https://github.com/responsibleai/ASSERT/commits/main
