# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

### Changed

### Fixed

## [0.3.0] - 2026-09-04

Adds evidence-backed eval authoring, stronger host-authoritative sandbox mediation, a broader atomic behavior library, and cleaner optional dependency ownership. This release also changes the default multi-turn budget and includes security, compatibility, and viewer fixes.

### Added

- Evidence-backed eval config generation for the `run-assert-eval` skill, including primary-source research, citation review, human approval before write, and one-risk-per-suite output (#337).
- Atomic behavior presets with broader built-in risk coverage and CI checks that enforce atomicity and parity with the example behavior specs (#293).
- Clean-install checks for every documented ASSERT/example environment, including `pip check` and credential-free target imports (#336).
- Real Docker containment tests that run on every pull request and push to `main` (#328).

### Changed

- **Breaking:** optional dependencies now describe ASSERT capabilities rather than repository examples. `otel` is replaced by `phoenix`, `azure-aad` by `azure-auth`, and `embeddings` remains the offline/local embedding backend. The `langgraph`, `dspy`, `examples`, and `regression` extras are removed; install each agent framework from the target project's dependency manifest or the repository example's adjacent `requirements.txt`. `all` now means all optional ASSERT product capabilities: `phoenix`, `analysis`, `embeddings`, `azure-auth`, and `acs` (#336).
- OpenInference instrumentors are owned by the runtime they instrument. ASSERT installs its LiteLLM instrumentor directly for Prompt Agent tracing; LangChain, OpenAI, and other framework instrumentors live with their target/example dependencies.
- Analysis keeps NumPy and the OpenAI embedding backend, while Phoenix owns the Pandas dependency used to convert its DataFrame spans.

| 0.2 install | 0.3 replacement |
|---|---|
| `assert-ai[otel]` | `assert-ai[phoenix]` plus the target framework's OpenInference instrumentor |
| `assert-ai[azure-aad]` | `assert-ai[azure-auth]` |
| `assert-ai[embeddings]` | Unchanged; use for offline/local Sentence Transformers embeddings |
| `assert-ai[langgraph]` | Target-owned dependencies or an example's adjacent `requirements.txt` |
| `assert-ai[dspy]` | Target-owned dependencies or an example's adjacent `requirements.txt` |
| `assert-ai[examples]` | Each example's adjacent `requirements.txt` |
| `assert-ai[regression]` | `uv sync --group dev` from a repository checkout; SciPy is no longer required |

- **Breaking:** `pipeline.inference.max_turns` now resolves to `6` instead of `10` when omitted. Scenario cases that relied on the implicit budget run four fewer turns, which can lower observed violation rates for multi-turn erosion harms, so results from before and after this change are not directly comparable. Set `max_turns: 10` explicitly to keep the previous behavior. Single-turn `prompt` cases do not use this setting and are unaffected. Because the resolved value feeds the inference resume fingerprint, a run interrupted before the upgrade re-executes rather than resumes (#337).

### Fixed

- Sandbox policy evaluation, mock resolution, and action evidence can now run in an authenticated host service instead of trusting the evaluated target to report its own decisions (#329).
- Sandbox mediation now propagates and validates one case ID across policy selection, mocks, execution, and evidence (#327).
- Proxy-generated network evidence is stored in a host-only directory outside the evaluated target's writable mount (#326).
- `assert-ai --version` now reads the installed distribution metadata instead of reporting a hard-coded stale version (#334).
- Declare `aiohttp`, which is directly used by the HTTP endpoint target, instead of relying on LiteLLM to install it transitively (#336).
- Update LiteLLM to patched version 1.84.1 and cap it below 2.0 (#330).
- Update vulnerable website, viewer, and Python dependencies to patched releases (#335, #336).
- Keep Bank Manager's GPT and non-GPT Azure routes compatible with ASSERT's OpenAI dependency range, and verify its documented installation with `pip check` (#336).
- Traced callable targets that capture no spans now record an actionable invalid-trace warning instead of silently reporting valid trace metadata (#337).
- Keep viewer result tables within their containers, wrap or truncate long labels, and position metric dropdowns so overflow cannot clip their options (#342).

## [0.2.0] - 2026-08-14

First release after the initial public launch. Adds an ACS guardrail
adapter, native Azure AD / Managed Identity auth for `azure/*` and
`azure_ai/*` targets, sandboxed action mediation, richer judge
configuration, and a `run-assert-eval` skill with Copilot / Claude / Cursor
front-doors.

### Added

- ACS guardrail adapter (`assert-ai[acs]` extra): turn a completed run's findings into a deployable Agent Control Specification policy via `assert-ai acs generate`, validate it against known-bad examples with `assert-ai acs validate`, and re-run a target guarded with the `guard_target` Python API. See `docs/guides/securing-agents-with-acs.md`.
- `assert-ai acs eval-config`: generate a small ASSERT config from an existing ACS manifest for regression/sanity checking an already-guarded target without requiring ACS runtime dependencies.
- Sandboxed action mediation (`target.sandbox`): start a disposable configured-agent container per test case with pass/mock/block tool policy, separate mock fidelity, deny-by-default audited egress, host-side model credentials, automatic cleanup, and judge-visible action/network evidence.
- Azure Managed Identity / Entra ID auth for `azure/*` models (#237) and native AAD support for `azure_ai/*` targets and Foundry hosted agents (#252) — machines with `az login` can now run ASSERT against Azure OpenAI without an API key.
- Custom rubric scales for judge dimensions (#264).
- Not-applicable state for custom judge dimensions (#261).
- Option to disable built-in judge dimensions (#248).
- Configurable test-set sampling methods (#269).
- Science regression gate (#263).
- `run-assert-eval` skill with Copilot / Claude / Cursor front-doors (#258), atomic worked examples (#311), and a smoke-run of 3 real test cases before full inference (#317).
- LangGraph Foundry hosted-agent example (#250).
- OTel GenAI (`gen_ai.*`) span mapping in the trace parser (#238), with hardened direct-GenAI extraction and fallbacks (#301, #249).
- Viewer: headline policy violations split by behavior (#295).
- Docs site: copy-to-clipboard buttons and content search (#247), CI gate entry point and ACS CLI reference (#281).
- Repo hygiene: stale-issue/PR bot (#257), scheduled review-escalation Action with always-on-host docs (#232), maintainer-assist agent pattern (#230), CODEOWNERS (#229).

### Changed

- Finished the permissibility metric transition (#305) and its policy-violation split in the viewer (#276, #295).
- Finished the public-terminology cleanup pass (#266).
- Example eval configs upgraded to the `gpt-5.4` family with `temperature=1.0` (#254).
- Centralized the lazy auto-tracing helper and swept the customer-facing surface to use it (#224).

### Fixed

- Isolate stateful mock backends per sandbox case and prevent the untrusted
  target container from reaching unrelated Docker-host services.
- `init` now emits YAML block scalars for multi-line strings and non-ASCII content (#314).
- `library show` writes YAML as UTF-8 bytes to stdout so non-ASCII values survive on Windows and under `PYTHONIOENCODING=ascii` (#314).
- YAML round-trip now preserves U+0085 / U+2028 / U+2029 by forcing double-quoted style for strings that contain them (#314).
- Viewer `compare` falls back to scenarios when a suite has no prompt samples (#270).
- `incident_triage_agent` example is now baseline-only with one behavior per YAML (#271); the older ACS demo variant is superseded by #262.
- Applied validated Codex security findings (#283).
- Docs site: rewrite repo-relative `.md` links so cross-references resolve on `responsibleai.github.io` (#225); repair broken links and remove missing logo asset (#227); remove maintainer-agent docs from the public site (#251).
- Docs / packaging polish: absolute README links for PyPI (#214), static MIT badge (#213), redeploy site when `docs/` or `assets/` change (#212), local viewer guide typos (#208, #210).

[Unreleased]: https://github.com/responsibleai/ASSERT/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/responsibleai/ASSERT/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/responsibleai/ASSERT/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/responsibleai/ASSERT/releases/tag/v0.1.0
