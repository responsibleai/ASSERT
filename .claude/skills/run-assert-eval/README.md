# run-assert-eval skill

Take a developer from **"I don't know my risks"** to a **measured violation rate
per risk** — without leaving the coding assistant. Risk discovery is owned by
**Clarity** (microsoft/clarity-agent) when the user wants it; measurement is owned
by **ASSERT** (responsibleai/ASSERT). This skill wires the two together, and also
measures risks the user names directly when discovery isn't what they need.

## Files

| File | Purpose |
| --- | --- |
| `SKILL.md` | Claude Code skill entry (the canonical instructions). |
| `../../.github/prompts/run-assert-eval.prompt.md` | GitHub Copilot mirror. |
| `../../.cursor/rules/assert.mdc` | Cursor mirror. |
| `workflows/measure-clarity-failures.md` | The 9-step measurement workflow (parse → triage → configs → run → report → close loop → curate example). |
| `workflows/govern-and-remeasure.md` | The ACS governance workflow: turn a measured failure into a deployable ACS policy (`assert-ai acs generate`), wrap the agent, and re-run the same eval to prove the failure rate dropped. |
| `workflows/diagnose-acs-delta.md` | Fallback reference manual for when a governed run's delta comes out wrong (no drop, or over-gating rose) — symptom-indexed, 15 rules. Most are prevented by the pre-flight classification in `govern-and-remeasure.md` Step 1a. |
| `clarity_intake.py` | Dependency-free parser: Clarity failure docs → ASSERT candidate behaviors. |
| `tests/` | Pytest suite + real Clarity fixtures for the parser. |
| `SETUP-CHECKLIST.md` | One-time in-IDE MCP setup + end-to-end verification. |

Keep the three skill surfaces (`SKILL.md`, the Copilot prompt, the Cursor rule)
methodologically aligned when changing the flow.

## Architecture

1. **Discovery (Clarity, shipped — recommended, not required):** the Clarity **MCP server** exposes tools —
   `run_clarity`, `write_protocol_document`, `record_failure`, `record_suggestion`,
   and others. `run_clarity` returns Clarity's real process guide inlined; the host
   agent conducts the clarifying conversation and persists findings. See
   `SETUP-CHECKLIST.md` to wire it up. When the user would rather name the risk
   themselves — or Clarity isn't set up — the skill takes a user-supplied risk
   (prose, PRD, design doc, threat model) through a structured intake instead
   (`SKILL.md` Step 1b) and everything downstream is identical.
2. **Handoff (files, not JSON):** Clarity writes `.clarity-protocol/`. The
   measurement side reads `failures/failures.md` (index) and `failure-NN-*.md`
   (individual docs). Those files are the **source of truth**; the parser's JSON is
   a disposable cache. The directory is **gitignored, single-domain scratch**.
   Before another discovery run overwrites it, let the user export it to a
   user-owned location if they need the raw record. Do not commit discovery
   workspaces into `examples/`; examples keep only curated configs and docs.
3. **Measurement (this skill):** `clarity_intake.py` turns failure docs into
   candidate behaviors; `workflows/measure-clarity-failures.md` runs a **mandatory
   human triage gate**, generates **one flat `evals/<atomic_behavior>.yaml` per
   selected failure**, runs them sequentially, and reports one behavior per column.
4. **Governance (ACS, optional):** when a run surfaces a real failure the user wants
   to *fix and prove*, `workflows/govern-and-remeasure.md` first **classifies the
   failure against the baseline** (Step 1a — semantic `output` gate vs. structural
   tool gate, and whether the harm actually routes through the tool being gated),
   then derives a deployable **ACS** policy from the findings
   (`assert-ai acs generate`), wraps the agent's high-risk tools (or its output),
   and re-runs the **same** eval against the governed target to show the
   failure-rate delta (baseline → governed). If that delta comes out wrong,
   `workflows/diagnose-acs-delta.md` is the symptom-indexed fallback.

## The parser (`clarity_intake.py`)

```
python .claude/skills/run-assert-eval/clarity_intake.py .clarity-protocol
```

Per failure mode it emits a `CandidateBehavior`:
`{name, description, severity, priority, source_doc, candidate_dimensions,
multi_behavior, suggested_splits, warnings}`.

- **Severity → priority**: Critical→P1, High→P2, Medium→P3, Low→P4. Ranges (e.g.
  `Medium–Critical`) collapse to the **maximum** severity.
- **Dimensions**: the doc's **Variants** list → an `elicitation_variant` stratify
  dimension (highest value — each variant is a distinct route to the failure);
  **Failure Chain** conditions → an `interaction_condition` dimension.
- **Atomicity**: docs that bundle several independently testable behaviors are
  flagged `multi_behavior` with `suggested_splits` so triage can surface the split.
- **Tolerant**: unknown severity labels or missing headers degrade to a **flagged**
  candidate (`warnings` populated) — never a crash, never a silent drop.

Run the tests:

```
python -m pytest .claude/skills/run-assert-eval/tests/test_clarity_intake.py
```

## Worked example

A full end-to-end walkthrough (one P1 — `user_disengagement` — from parse through
triage, config generation, run, headline metrics, and closing the loop) lives in
`workflows/measure-clarity-failures.md` under **Worked example (one P1)**. The ACS
governance counterpart is in `workflows/govern-and-remeasure.md`.

## Related ASSERT docs

Product behavior is documented under `docs/` (team-maintained, on `main`); the skill
**links** rather than restates it — `guides/create-evaluation.md` + `config/schema.md`
(config authoring), `targets/callable.md` (callable signature, return types, OTel
auto-instrumentation) + `targets/model-and-tools.md` (target shapes),
`guides/troubleshooting.md`, `guides/results.md`, `guides/use-local-viewer.md`, and
`guides/securing-agents-with-acs.md` (the ACS loop). This skill owns the *methodology*;
those own *product behavior*. The exceptions the skill documents itself are the two
callable traps those docs omit: `history` is detected by parameter **name** (misnaming it
silently degrades multi-turn to single-turn), and module resolution falls back
`sys.path` → config dir → cwd → direct file load.

## Guarantees the skill enforces

- One atomic behavior per config — never bundle.
- The triage gate and the pre-run confirmation are **human** decisions; declining
  writes nothing and runs nothing.
- `.clarity-protocol/` files are authoritative; derived JSON is a cache.
- Clarity discovery is **recommended, never a gate** — the user picks the risk
  source, and a missing `.clarity-protocol/` never blocks a measurement.
- When the user chooses Clarity, discovery goes through its real MCP tools — never
  an imitation of Clarity's interview, no shelling out to a `clarity cli` process,
  no separate app.
- Never read/print/commit `.env`, credential values, or `artifacts/`.
