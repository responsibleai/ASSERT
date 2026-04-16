# P2M Copilot Context

Do not change - never - the instructions right between --- instructions ---

--- instructions ---
No hardcoding unless necessary.
Keep this compact and focused on the most important guidelines for contributors. 
No backward compatibility unless you are told so. Break fix, then we will adjust. 
Big changes -> commit unless you are told otherwise. 
Keep documentation always up to date. 
Simplify the codebase and remove old code when possible. do not introduce abstractions unless necessary.
I do not want fallbacks unless you are told so. 
Write tests whenever possible.
Always check for unused code and remove it. Before committing. 
Always follow the instructions in the cleanup playbook. Do not wait for a cleanup PR. If you touch a file, clean it. The user must be able to read and understand it without jumping through hoops, super quickly. 
readmes should be understandable by a pm. Use standard language and avoid jargon. Keep them short and to the point.
When evaluating if the repo is prod ready, ask yourself: can a new contributor read the README, understand how to run an evaluation, and then understand the codebase well enough to make a small change without asking for help? If the answer is no, keep iterating on the documentation and code until it is yes. 
Then ask yourself: can a PM read the README and understand how to run an evaluation? If the answer is no, keep iterating on the documentation until it is yes.
--- instructions ---

---

## Section 1: Repository Guidelines

**Structure:** `p2m/` is the primary package: `cli.py`, `config.py`, `runner.py`, `core/`, `stages/`, `analysis/`. `examples/` has risk texts, YAML configs, sample drivers. `prompts/` has prompt templates. `viewer/` is the SvelteKit results browser. `tests/` for pytest. `docs/` for longer-form notes. `scripts/` is shell helpers only.

**Commands:**
```bash
uv venv && uv sync                                           # setup
uv run p2m run --config examples/pipes/health_assistant.yaml  # canonical example
uv run pytest -q                                              # tests
cd viewer && npm install && npm run dev                       # viewer dev
cd viewer && npm run check && npm run build                   # viewer build
```

**Style:** Python 3.11+. 4-space indent. Explicit type hints on public helpers. `snake_case` for Python/YAML. `PascalCase` for Svelte. Conventional Commits (`fix:`, `feat:`, `refactor:`, `docs:`).

**Cleanup:** Optimize for reading experience. Inline single-use helpers. Remove alias variables, dead config keys, dead wrappers, fallback code. One parsing path, one prompt path, one config path. Constants at the top. Tools: `rg`, `uv run pytest -q`, `ruff check --fix`, `ruff format`, `pyright`, `vulture`. Update `docs/architecture-map.md` if control flow changes.

**Testing:** Pytest with `unittest` classes. Name tests after behavior. Focus on pipeline I/O, artifact schemas, judgment aggregation, model-client normalization. Run `uv run pytest -q` before PRs.

**Docs:** Two audiences: PM (what it does, how to run) and contributor (how to change). Separate tutorials, how-to, reference, explanation — never mix. Ban filler words. Code examples must be real and runnable. One name per concept. README gets a reader to a working result in under a minute.

**Security:** Credentials from env vars or `.env`, never committed. `artifacts/`, `logs/`, `.venv/`, `node_modules/` stay out of version control.

---

## Section 2: Project Context

**What this is:** P2M — requirement-driven adaptive evaluation. Pipeline: `Requirement Map → Generation → Rollout → Judge → Metrics/Artifacts`. Surfaces: YAML config, CLI (`uv run p2m run --config <config>`), thin Python SDK under `p2m/`.

**Direction:** Ship as standalone OSS. Optimize for: local-first, no lock-in, framework neutrality, minimal adoption friction, CLI + YAML ergonomics. Evaluate Foundry integration later after adoption is proven.

**Agent Shield:** Design reference for product shape and docs style. Shared goals: YAML-first contracts, thin SDK adapters, local-first, portable. Future: Adaptive Evaluation discovers failure modes → Agent Shield applies runtime controls → **eval-fix loop**.

**Terminology** (do **not** frame adaptive evaluation as safety-only):

| Old | New |
|---|---|
| Risk | Failure Modes |
| Risk Definitions | Failure Mode Definitions |
| Suite | Dataset |
| Pipeline | Generation |
| Taxonomy | Requirement Map |
| Seeds | Test Cases |
| Audit | Conversations |

**Spec style:** Follow `agent-shield/spec/SPECIFICATION.md`: Version/Status/Last Updated header, Abstract, numbered sections, precise declarative engineering language, tables for stages/interfaces/terminology, implementation-aware but framework-agnostic.

**JTBD:** Engineers building agents/model apps/eval pipelines → define requirement-specific failure modes and test them quickly → current tools fail because static evals are shallow, fragmented, platform-locked → provide YAML-first authoring, CLI-first execution, portable artifacts, framework-neutral rollout → evaluation output drives Agent Shield mitigations.
