---
agent: agent
description: 'Run an ASSERT evaluation against a described risk. Risks come from Clarity (recommended — drives the real Clarity MCP tools (run_clarity) in-IDE to discover failure modes the user has not considered) or directly from the user as a description, PRD, design doc, threat model, red-team finding, or risk assessment. Researches how that risk has been evaluated in the literature, generates one evidence-backed, cited, user-approved examples/<domain>/<risk>/eval_config.yaml per selected risk, runs the assert-ai pipeline, and reports per-dimension pass/violation rates with trace-cited failure examples.'
---

# Run an ASSERT evaluation

You help the user run an end-to-end ASSERT evaluation. Risks come from Clarity (recommended) or directly from the user. You drive the existing Clarity **MCP tools** and `assert-ai` CLI — you do not reimplement Clarity's questioning or any pipeline logic.

Read `AGENTS.md` at the repository root for full orientation on the ASSERT project, terminology, and target selection.

## When to use

The user wants evidence of how their agent or model actually behaves. This skill finds and reports failures — it is not for fixing the agent.

This skill has two entry modes:

- **Run mode** — no usable results exist yet. Establish a **risk source** (Steps 1-2): **Clarity** (recommended) — an existing `.clarity-protocol/` directory or a fresh discovery run via the Clarity MCP `run_clarity` tool, driven in-IDE — **or risks the user supplies directly**. Then turn each selected risk into an atomic config, run the pipeline (Steps 3-5), then report (Step 6).
- **Results Q&A mode** — judged artifacts already exist under `artifacts/results/<suite>/<run>/` and the user asks a *question* about them ("what are the highlights?", "top 3 examples of the worst failure mode?", "why did case X fail?"). Skip to Step 6 and answer THAT question from the artifacts — do not re-run, and do not fall back to the full canned report unless asked.

### Choosing a risk source (Clarity recommended, never required)

Every eval starts from a risk. There are two supported sources, and **the user chooses** — never decide for them and never block on Clarity.

- **Path A — Clarity discovery (recommended — present it first, but never alone).** An existing `.clarity-protocol/` or a fresh run via `run_clarity`. Clarity's value is finding failure modes the user has *not* thought of, plus severity and causal chains. Recommend it whenever the user is unsure what to measure or wants coverage rather than one known bug.
- **Path B — user-supplied risks.** The user names the risk themselves, as prose or by pointing at a PRD, design doc, threat model, red-team finding, incident report, or risk assessment. Right when they already know what they want measured.

**Both paths answer *what* to test for; neither answers *how*.** That is Step 3's job: once a risk is named, the research procedure reviews **how that risk has actually been evaluated** and converts the findings into the test-set design — the timescale it becomes observable on (psychosocial harms usually need `scenario`, not one answer), whose viewpoint the probes are authored from (a hospital helpdesk is exercised by patients, nurses, and schedulers, not one adversarial persona), and which conditions the evidence says change it.

**Whenever you need a new risk to measure**, and the user has not already named one, **offer the choice**: "I can find a risk two ways. **Clarity** interviews you and surfaces failure modes you may not have considered — recommended when you know the agent but aren't sure what to measure. Or **you name it directly**, in your own words or by pointing me at a PRD, design doc, threat model, red-team finding, or risk assessment — best when you already know what you want measured. Either way I then research how that risk has been evaluated and build the test set from that evidence. Which do you prefer?" An existing `.clarity-protocol/` changes the **default**, never the **choice** — offer it as the recommended option ("I found an existing Clarity protocol with these risks — measure one of those, or is there a different risk you have in mind?"), then take the user's answer. Any menu that offers Clarity must carry Path B beside it.

Rules on both paths: **an explicit user-supplied risk always wins** — if the user names a risk, in prose or by pointing at a document, measure *that*, whether or not a `.clarity-protocol/` exists; never substitute the protocol's risks for one the user just stated, and if you think the protocol covers the same ground, say so and let them decide. Never silently pick a path, and never stall the user on Clarity setup — if the MCP tools are missing and they'd rather not set them up now, take Path B. **Do not imitate Clarity's interview from your own head**: if they picked Path A, drive the real `run_clarity` tool; Path B is a distinct structured intake (Step 1b), not a hand-rolled impression of Clarity. Path B meets the same quality bar (atomic behaviors, an explicit permissible boundary, researched stratify dimensions, pinned systematize/judge models, explicit `sample_size`) — Steps 3-6 are risk-source agnostic. **The skill does not invent risks**: risk identification is Clarity's job or the user's, and Step 3's research never substitutes for deciding what to measure. Offer Clarity again later; declining once is not a permanent opt-out. Clarity write-backs (`record_failure` / `record_suggestion`) degrade to no-ops when no protocol exists — skip them and say so once, never treat their absence as an error.

### Copilot vs. the local viewer

Copilot is for *answering questions* and *synthesis* — direct answers, failure-mode clustering, cited examples, next actions — with no clicking. The bundled local viewer is for *visual exploration* — forest plots, baseline compare, facet grouping, and stepping through a transcript with the judge's citations highlighted. Answer in chat when the user asks "what / why / which"; hand off to the viewer (Step 7) when they want to *see*, *read a full transcript*, *compare runs*, or *watch a live run*.

## Preconditions (check, don't assume)

1. **ASSERT installed**: verify `assert-ai --help` succeeds. If not, guide install from PyPI — not an editable install of the user's own repo:
   ```
    python -m pip install "assert-ai[phoenix]"
   ```
    The target project owns its agent framework and OpenInference instrumentor dependencies. For repository examples, install the adjacent `requirements.txt`; for a customer project, use that project's existing dependency manifest. `target.endpoint` needs `aiohttp`, which ships with ASSERT. Use `pip install -e ".[phoenix]"` **only** when the working directory is a clone of the ASSERT repo itself; inside a customer repo it installs the wrong package.

2. **Clarity MCP server available** (needed only for Path A): the `clarity-agent` MCP tools (`run_clarity`, `write_protocol_document`, `record_failure`, `record_suggestion`, …) are callable in this session. Clarity is the risk-discovery engine — the skill drives its real MCP tools, it does not reimplement it. If the tools are missing, the server is not wired up yet: offer `SETUP-CHECKLIST.md` (install `clarity-agent` with the `[mcp]` extra, run `clarity embed .` to generate `.vscode/mcp.json`, reload MCP servers) and confirm the LLM provider is configured (`clarity doctor` — Clarity supports GitHub Copilot, Anthropic, OpenAI, Azure AI, and Gemini). **This is not a blocker**: if the tools can't be made available, or the user would rather not set them up now, say so plainly and continue on Path B (Step 1b). Never strand the user on MCP setup when they came to measure something.

3. **Provider creds exist** in `.env`. NEVER read or print `.env`. If a run fails with an auth error, tell the user which variable NAMES are required (AZURE_API_KEY, AZURE_API_BASE, OPENAI_API_KEY, GITHUB_TOKEN, ANTHROPIC_API_KEY, etc.) — never their values.

## Steps

### 1. Establish the risk source

Ask which path the user wants (see "Choosing a risk source" above), then follow **1a** or **1b**. If the user already named a risk (prose, PRD, design doc, threat model, incident report, test plan), that is an explicit Path B choice — go to **1b** even if a `.clarity-protocol/` exists, and do not silently switch to the protocol's risks. If intent is ambiguous and a protocol exists, offer it as the default and say what's in it, but still ask before selecting it: *"I found an existing Clarity protocol covering X and Y — want to measure one of those, or is there a different risk you have in mind?"* If intent is ambiguous and no protocol exists, offer the choice as written above.

#### 1a. Clarity discovery (recommended)

Risks come from Clarity's real engine, driven through the **Clarity MCP server** — never by imitating Clarity's interview from your own head.

- **If a `.clarity-protocol/` directory already exists** in the workspace, and the user has chosen Path A for this risk, use it directly as the risk source — skip straight to reading its output below. (Selecting Path A is the user's decision, made in Step 1; the protocol's presence alone does not make it.)
- **Otherwise run discovery via the Clarity MCP tools:** call **`run_clarity`** (it returns Clarity's real process guide inlined as text), follow that guide to ask the user the clarifying questions **in chat**, and persist findings with **`write_protocol_document`** and **`record_failure`** until `.clarity-protocol/failures/failures.md` is written. (Copilot agent mode supports MCP *tools*, so drive the loop yourself rather than expecting a separate chat UI.)

Read Clarity's output to enumerate risks:

- **`.clarity-protocol/failures/failures.md`** — the failure modes, causal chains, and management plans. Each distinct failure mode is one candidate ASSERT behavior.
- **`.clarity-protocol/summary.md`, `goal/requirements.md`, `solution/architecture.md`** — target/context for the eval's `context` field.

**For the full measurement path** — parse → triage → one atomic config per selected failure → sequential runs → report → close the loop → curate the example — follow `../../.claude/skills/run-assert-eval/workflows/measure-clarity-failures.md` and use the intake parser (`clarity_intake.py`) to convert `failures.md` into candidate behaviors (severity→priority, variant-derived stratify dimensions).

> **Before a fresh discovery run, check the preservation gate.** `.clarity-protocol/` is gitignored, single-domain scratch; `run_clarity` **overwrites** it. If another domain's protocol is present, STOP and let the user export it to a user-owned location or explicitly discard it. Never commit the raw discovery workspace into `examples/`.

Clarity records severity/management-plan signal (the parser maps Critical→P1, High→P2, Medium→P3, ranges→max) — order and annotate by what Clarity actually captured; do not fabricate priorities.

#### 1b. User-supplied risks

Turn the user's input into the **same candidate-behavior shape** `clarity_intake.py` produces on Path A — `{name, description, severity, priority, source_doc, candidate_dimensions, multi_behavior, suggested_splits}` — so Steps 2-6 are identical either way.

1. **Take the input as given.** If they point at a PRD, design doc, threat model, incident report, or test plan, read it and extract candidate risks from it, quoting what you extracted so they can correct you. If they describe it in prose, start from their words — do not paraphrase the risk into something broader.
2. **Ask only what the config actually needs**, in one batch, skipping anything already answered by their input or the repo:
   - *What does the agent do, and who uses it?* → `context`
   - *What must it never do?* → `behavior.name` + `behavior.description`
   - *What is it explicitly allowed to do that looks similar?* → the permissible boundary. **Do not skip this**: without it the judge cannot separate real harm from over-refusal, and both rates become uninterpretable.
   - *How would a user trigger this — innocently, and deliberately?* → the `elicitation_variant` stratify dimension, the highest-value one
   - *How bad is it when it happens?* → severity → `priority`
3. **Enforce atomicity now.** If their description bundles several independently testable behaviors ("it leaks data and hallucinates prices"), say so and propose the split — one config each. This is the `multi_behavior` / `suggested_splits` check, applied by hand.
4. **Play it back for confirmation** as an explicit candidate list before generating anything, exactly as triage does on Path A.

Set `source_doc` to the file you read, or `user-described` when it came from chat. Record severity as the user rated it; do not invent a priority they didn't give.

**For the full measurement path** — triage → one atomic config per selected risk → sequential runs → report → curate the example — follow `../../.claude/skills/run-assert-eval/workflows/measure-clarity-failures.md`, the same workflow Path A uses. **Skip its Step 1 (Parse)** — there is no `failures.md`, so join at Step 2 with the candidate list you just built — and skip its Step 8 (close the loop in Clarity) unless a `.clarity-protocol/` exists. Everything else downstream is unchanged.

### 2. Triage — choose which risks to measure now

Clarity intentionally over-produces (whole-lifecycle threat modeling). Do NOT auto-generate an eval for every failure mode. Surface the enumerated list (ordered by severity signal) and ask the user which to measure now (e.g. "top-severity only?", or named picks). Carry only the selected risks forward. On Path B the list is usually short and already chosen — still play it back and confirm scope before generating configs.

### 3. Turn each selected risk into an atomic config

ASSERT performs best with **one atomic behavior per eval**. Never bundle multiple risks into one config — bundling makes `policy_violation` a fuzzy logical-OR and hides per-behavior signal.

- **1 selected risk** → generate one config and run once.
- **N selected risks** → generate N configs and run them sequentially, one per behavior.

Configs are **researched, cited, and user-approved** — not scaffolded and hoped for. Follow `../../.claude/skills/run-assert-eval/workflows/research-eval-dimensions.md` for each selected risk; it owns the whole of config generation. The risk is already named by the time you get here — what the research supplies is **how that risk has been evaluated**, converted into the test-set design. Collect one input first and **never silently default it**: `N` (positive integer — how many complete dimension-generation passes to run before deduplication).

That workflow: reuses a repo behavior preset where one matches (which settles the harm's stable slug); runs a path-only isolation preflight on that slug (`plan_generation_path.py`) that never reads a prior generated YAML; researches the dimension model against retrieved primary sources with a ≥2-independent-source gate; runs `N` complete passes and semantically deduplicates across three namespaces (behavior categories, stratify dimensions, judge dimensions); blocks on **explicit user approval** (silence is not approval, enforced by `validate_dimension_review.py pre-write`); then writes a cited config to `examples/<domain>/<risk>[_YYYY-MM-DD]/eval_config.yaml` with inline `# sources:` comments and a `# References` block.

Prefix the eval **suite name** with a domain slug (`<domain>-<risk>`) so `artifacts/results/<suite>/` and `artifacts/acs/<suite>/` don't collide.

- **Pin `systematize` and `judge` to the strong model.** Run the eval cheap and the two ground-truth stages strong — `default_model.name: azure/gpt-5.4-mini` (target, test-set, tester) plus `pipeline.systematize.model: azure/gpt-5.4` and `pipeline.judge.model: azure/gpt-5.4`. This is the convention in the repo's own `examples/` configs. `systematize` authors the behavior tree and the permissible / non-permissible split that **every** metric is computed against, and `judge` decides both applicability and violation per row on a single sample (`judge.n` defaults to `1`, judge temperature unpinned) — a weak model there moves the target rather than adding noise around it, and inflates run-to-run applicability drift. Verify after the run with `assert-ai results status <suite> <run> --json` → `prompt_metrics.judge_model` / `scenario_metrics.judge_model`.
- **Reuse repo presets first** — `assert-ai library list` shows bundled behavior and judge presets (`prompt_injection`, `doxxing`, `stereotyping`, `sycophancy`, `harmful_medical_advice`, `violent_content`, `sexual_content`, `hate_speech_harassment`, `malicious_cyber_activity`, …); `assert-ai library show <name>` prints one. Prefer `behavior.preset` or a copy-in spec from `examples/behavior_specs/` over reinventing a description.
- **`behavior_category_count` is `25`** — the standard count, and ASSERT's own default (`DEFAULT_BEHAVIOR_CATEGORY_COUNT`). Research shapes *which* categories are generated, not how many.
- **Ask the user for the `sample_size` — do not pick it silently, and never accept a value below `behavior_category_count` (so `≥25`).** Below the category count some behavior categories receive zero cases and `coverage_at_k(…, k=1, …)` cannot reach `1.0`, so a harm living in an unsampled category reads as absent. Each rate is also `violations / sample_size`, so even at the `25` floor one flipped case = ±4pp of noise, growing as the sample shrinks, and since inference is non-deterministic (agent temperature 1.0; gpt-5 can't be pinned lower) two runs of the same config drift by chance. Ask e.g. *"How many cases per behavior? `25` = the floor and the recommendation, `50`+ = tightest signal — I'll use the same size for prompt and scenario."* The floor protects coverage, not precision — prefer `50`+ for a small expected delta. The governed config is a byte-identical copy that inherits this size — see `govern-and-remeasure.md`. Cost scales linearly.
- **`max_turns` is fixed at `6`** — ASSERT's default (`DEFAULT_TESTER_MAX_TURNS`) and the config template's value. It is not research-derived: the evidence chooses the test *mode* (`scenario` vs `prompt`), not the turn budget, and a genuinely single-turn harm uses `prompt` cases, which ignore the knob. Keep it **identical in baseline and governed configs** or the "only ACS differs" comparison breaks.
- **Author researched judge `dimensions`, but never reuse a built-in name.** `policy_violation` and `overrefusal` are `BUILT_IN_DIMENSIONS` (`assert_ai/core/judge.py`) and are always judged unless explicitly disabled; the researched harm-specific dimensions are added **on top** of them. Config dimensions merge over the built-ins **by name** into the same dict, so declaring one called `policy_violation` or `overrefusal` silently replaces the built-in rubric. That does **not** move the headline permissibility split — `assert_ai/results.py` recomputes it from the per-behavior `node_judgments`, never from a dimension verdict — but it does change the verdict stored in the run JSON and `DEFAULT_COMPARE_METRIC`, so stored results stop meaning what the engine documents. **The same applies to `judge.preset`, which expands into that same merged list: do not use `safety-core`** — its only two dimensions are named exactly `policy_violation` and `overrefusal`, so it replaces both built-in rubrics and buys nothing: `_SUPERSEDED_DISPLAY_METRICS` (`assert_ai/cli.py`) hides that pair from CLI detail surfaces whenever the split is available. `safety-extended` is additive (`harm_actionability`, `pii_leakage`) and is safe. The post-write gate rejects both forms.
- **If live source retrieval is unavailable**, say so and stop at the ledger. The evidence gate can't be met without it, and a config with remembered or invented citations is worse than no config. `assert-ai init --default-model <litellm-model> --describe-file <path> --non-interactive -o <path>` remains available as an explicitly unvalidated scaffold — never for a measurement you intend to report or govern against.
- After generation, show the user the resolved `behavior.description`, `context`, `pipeline.judge` settings, the `systematize` / `judge` models, and the reference list. Confirm before running.

### 4. Identify the target shape

Help the user set the right target in the config:

- **Framework agent** (LangGraph, CrewAI, etc.) with a Python entry function: use `target.callable` WITH `target.trace` so the judge can cite tool calls and routing.
- **Hosted model** with a system prompt and optional tools: use `target.model` and `target.tools`.
- **Pre-collected traces** (no live inference needed): use `assert-ai judge-traces --traces <path> --config <path>`; do not add a `--trace` flag to `assert-ai run`.
- **Black-box HTTP endpoint** you cannot import as Python: use `target.endpoint` — the runtime POSTs `{"message": ..., "history": [...]}` and reads `{"response": ...}`, so no wrapper code is needed (requires `aiohttp`). Only write a thin `target.callable` shim if the service's request/response shape differs. Either way the judge sees only final text, so this is a fallback, not the recommended path.

**The callable contract — verify before the first run.** Full signature and return-type rules live in `docs/targets/callable.md`. Two behaviors that doc omits can silently corrupt a run:

- **`history` is detected by parameter *name*, not position.** Multi-turn is enabled only when a parameter is literally named `history`. Name it `messages`, `conversation`, or `chat_history` and every scenario **silently degrades to single-turn** — the run completes, the viewer renders, and the numbers are wrong with no warning. That invalidates the baseline and any ACS delta measured against it.
- **Module resolution has a four-step fallback**: `sys.path` → the config's own directory → the current working directory → direct file load. An `agent.py` beside the YAML config resolves even when the CLI runs from the repo root, but a same-named module earlier on `sys.path` wins — prefer a domain-unique module name over a bare `agent`.

**Why `target.trace` is not optional.** Judge visibility by integration path: a plain `str` return exposes 1 of 8 signals (final text only), a LiteLLM-style response 4 of 8 (adds final tool calls, token usage, model name), and OTel traces 8 of 8 (adds *intermediate* tool calls, routing / sub-agent decisions, intermediate model calls, per-span latency). Without traces a tool-misuse or wrong-routing failure is largely invisible to scoring. Use ASSERT's OTel auto-instrumentation (33 frameworks — LangChain/LangGraph, CrewAI, OpenAI Agents SDK, DSPy, LlamaIndex, AutoGen, MAF, Pydantic AI, …), a single helper call at the top of the callable module, rather than hand-writing spans.

### 5. Run the pipeline

**Offer a smoke run first.** Plumbing errors (wrong `callable`, missing credentials, a callable that raises on its first tool call, tool-schema mismatch, undeployed judge model) surface only once inference starts, after the upstream stages have already run. Validate on 3 real cases:

```
assert-ai run --config examples/<domain>/<risk>/eval_config.yaml \
  --override inference.enabled=false --override judge.enabled=false
python .claude/skills/run-assert-eval/smoke_slice.py \
  --config examples/<domain>/<risk>/eval_config.yaml --count 3
assert-ai run --config examples/<domain>/<risk>/eval_config.yaml \
  --override run=<run>-smoke --override inference.test_set_path=<out path>
```

If it fails, stop and report — do not start the full run. Three cases is not a measurement, so never report a rate from it. Never lower `test_set.sample_size` instead: that invalidates the cached test set and does not produce a subset. Detail in `.claude/skills/run-assert-eval/workflows/measure-clarity-failures.md` Step 5a.

```
assert-ai run --config examples/<domain>/<risk>/eval_config.yaml --output json
```

This is long-running (systematize -> test_set -> inference -> judge). Stream status to the user as each stage completes. For N configs, run them sequentially and track each `suite`/`run`. After a smoke run the first two stages report CACHED. Re-run from a stage with `--force-stage <stage>`. Note the `suite` and `run` names from the config for Step 6.

### 6. Report results — never collapse to one number

**Read only structured artifacts.** Aggregate from the pre-computed, schema'd files — never trawl raw Phoenix/OpenTelemetry traces to reconstruct an answer (that bulk, unguided trace-reading is exactly what the viewer's evidence drawer is for). Reading the `inference_set.jsonl` row for a *specific case the judge already cited* is fine; bulk trace trawling is not.

1. **Headline rates**: run `assert-ai results status <suite> <run>` for per-dimension flagged rates (split into prompt and scenario). Report the violation dimension and `overrefusal` SEPARATELY — they are two different problems. The built-in `policy_violation` ORs over ALL violated taxonomy nodes (permissible included), so it couples with `overrefusal`. The headline pair is the permissibility split: add `--json` and read `not_permissible_policy_violation_rate` (real harm got through) and `permissible_policy_violation_rate` (the agent broke a behavior it was allowed to do), each one vote per conversation. Headline both in an ACS A/B — harm should drop while permissible stays flat (see `govern-and-remeasure.md`). The viewer exposes the same pair as the dimension keys `policy_violation_not_permissible` / `policy_violation_permissible`, rendered on screen as **Harm (non-permissible)** / **Permissible behavior violated**.

2. **Top failing cases**: read `scores.jsonl` from `artifacts/results/<suite>/<run>/`. For each dimension with failures, pull 3-5 representative cases with:
   - The test case description (what was tested)
   - `verdict.dimensions` — which dimensions failed
   - `verdict.dimension_justifications` — the judge's rationale with cited evidence
   - `verdict.node_judgments` — which behavior categories were violated, with reasoning

3. **Cost and timing**: read `metrics.json` for token usage and elapsed time per stage. This file contains cost metadata only, not score roll-ups.

For **Results Q&A mode**, answer the user's specific question from these same artifacts (e.g. rank dimensions by flagged rate for "top failure mode", then quote `dimension_justifications` for the cited examples). Don't emit the full template unless asked.

### 7. Hand off to the local viewer

After reporting, point the user to the bundled viewer for anything visual or self-directed — it went through extensive design iteration and owns the exploration surface Copilot should not replicate:

```
cd viewer && npm install && npm run dev   # then open http://localhost:5174
```

Select the suite and run for forest plots, per-dimension breakdowns, facet grouping, the permissible vs. not-permissible policy-violation split (also available from `assert-ai results status --json` and rendered by `results compare`), and a transcript drawer with the judge's `[N]` citations highlighted on the cited turns. Suggest it specifically when the user wants to:

- **read a full transcript** or **see the trace** for a case → viewer evidence drawer
- **compare against a baseline** → viewer compare view (or `assert-ai results compare <suite> <runA> <runB>`)
- **watch a run in progress** → viewer live run monitor (`manifest.json`-driven)

See `docs/guides/use-local-viewer.md` for the full layout.

### 8. Govern the failure and re-measure (ACS)

When a run surfaces `policy_violation` failures and the user wants to **fix and prove it**, generate a deployable **ACS** (Agent Control Specification) policy from the findings and re-run the same eval against the governed agent to show the failure rate dropped — the ACS delta. Uses ASSERT's native `assert-ai acs generate` / `validate` adapter (no external `acs` CLI). Requires a **callable** target whose high-risk tools can be wrapped (`control.protect_tool`); a hosted-model Prompt Agent target has nothing wrappable. Follow `../../.claude/skills/run-assert-eval/workflows/govern-and-remeasure.md` (baseline → `acs generate` → `acs validate` → governed run → delta from two `results status --json` calls → export each run to standalone HTML → close the loop in Clarity). **Classify the failure before generating the policy** (Step 1a): read the baseline's `verdict.dimension_justifications` to decide semantic (`output` annotator) vs structural (tool gate), and confirm the harm actually routes through the tool you plan to gate — getting that wrong is the main cause of a gate that fires ~0 times. Always regenerate-and-re-gate on a deny (never a flat-refusal fallback, which is scored as overrefusal). If the delta still comes out wrong, `../../.claude/skills/run-assert-eval/workflows/diagnose-acs-delta.md` is the symptom-indexed diagnostic manual (cap ~4 attempts). Reference: `examples/billing_support_agent/agent.py` (baseline callable shape; the governed entrypoint is a workflow output, not checked in).

### 9. Hand off to CI

Once the eval is passing locally and the user wants it enforced on PRs, hand off to the `wire-assert-ci` skill. Do not author CI wiring here; direct the user or agent to the action bootstrap:

```
read https://raw.githubusercontent.com/responsibleai/assert-ai-action/main/ONBOARD.md
```

## Output format

Present a short summary with this structure:

**Headline metrics**:
- Harm — non-permissible violation rate: X% (N/M cases) [`not_permissible_policy_violation_rate`]
- Permissible behavior violated: X% (N/M cases) [`permissible_policy_violation_rate`]
- Overrefusal rate: X% (N/M cases) — the separate availability check

Report the permissibility split as the headline pair (from `results status --json`); the raw `policy_violation` rate ORs over all violated nodes and couples the two, so quote it only as context, never as the headline.

**Researched judge dimensions** (when the config declares them), each on its own line with its flagged rate — reported beside the headline pair, never merged into it:
- `<dimension_name>`: X% (N/M cases) — one-line gloss of what its rubric scores

**Evidence base**: the config's `# References` list (tag → title → URL), so the provenance of the dimensions being reported is visible alongside the numbers.

**Top failing cases** (3-5 per dimension):
For each failure:
- Requirement cited: [behavior category from taxonomy]
- Action cited: [specific turn or tool call from judge rationale]
- Judge rationale: [verbatim from dimension_justifications]

**Suggested next step**: one concrete action (e.g. "tighten the system prompt around X behavior", "add a stratify dimension for Y", or **govern the failure with ACS and re-measure to prove the rate dropped** — see Step 8 and `../../.claude/skills/run-assert-eval/workflows/govern-and-remeasure.md`).

**Non-ACS fixes are the user's call, outside this skill.** Troubleshooting a failure may lead a coding agent to a non-ACS fix — upgrading or swapping the target model, rewriting the agent's system prompt, or otherwise changing the agent itself. Those are legitimate but are *agent changes*, not ACS governance: ACS can constrain inputs, outputs, and tool calls, but it cannot add a capability the agent lacks. **Never fold one into the ACS-governed run.** Step 8's A/B is readable only because the governed run differs from the baseline in nothing but `run:` and `target.callable`; change the model or the prompt inside it and two variables moved at once, so the delta is attributable to neither — relabelling the run does not recover it. Measure an agent change as its own arm instead: branch from the same baseline, reuse its exact test set, and name that run for the change (e.g. `model-upgrade`).

## Authoritative references

Team-maintained docs under `docs/` on `main` — prefer them over restating product behavior here. `guides/create-evaluation.md` and `config/schema.md` (step 3), `targets/callable.md` and `targets/model-and-tools.md` (step 4), `guides/troubleshooting.md` (step 5), `guides/results.md` (step 6), `guides/use-local-viewer.md` (step 7), `guides/securing-agents-with-acs.md` (step 8). This skill owns the methodology — the Clarity → ASSERT → ACS → ASSERT loop; those docs own product behavior.

## Guardrails

- **Clarity is the recommended risk source, not a gate** — present **both** options together whenever the user needs a new risk: Clarity discovery (existing `.clarity-protocol/` or a fresh `run_clarity` run) *and* risks they supply themselves. Recommend Clarity, because it surfaces failure modes they haven't considered — but never present it as the only route. Any menu, list, or question you offer that includes a Clarity option must carry the user-supplied option beside it; a user who doesn't know Path B exists cannot ask for it. Hold the user-supplied path (Step 1b) to the same bar: atomic behaviors, an explicit permissible boundary, researched and cited dimensions. Never block a measurement on Clarity setup.
- **Never imitate Clarity's interview from your own head** — if the user chose Clarity, drive the real MCP tools (`run_clarity` returns its genuine process guide inlined). Step 1b is a distinct structured intake, not a hand-rolled impression of Clarity.
- **Drive the real Clarity MCP tools in-IDE** — use `run_clarity` / `write_protocol_document` / `record_failure` for discovery and `record_suggestion` to close the loop; never hand the user off to a separate Clarity app and never shell out to a `clarity cli` process.
- **Close the loop when a protocol exists** — after a run, offer `record_suggestion` (or `record_decision`) back into `.clarity-protocol/` noting the failure mode now has a measured baseline and where the eval lives. With no protocol, skip it silently — and consider offering Clarity as a next step for finding risks this pass didn't cover.
- **Govern with ACS, don't just prompt-tweak** — to fix and *prove* it, generate an ACS policy from the findings (`assert-ai acs generate`), **review and commit** it (scope the gated tools, tighten conditions), and re-run the same eval against the governed callable to show the delta; needs a wrappable callable target (`../../.claude/skills/run-assert-eval/workflows/govern-and-remeasure.md`). Whenever a gate needs a value the model doesn't put in the tool args — a trusted session flag (verification), a trusted comparison value (the caller's own id), a trusted numeric cap, or a running total / prior-call fact — the governed agent must surface that scalar from its **session state** into the tool-call **policy_target** so the generated `input.policy_target.value.*` rule actually fires. ACS evaluates each call in isolation, so multi-call constraints (running totals, ordering, rate limits) are handled by that same injection, not by encoding history in Rego. Free-form content failures (unsafe advice, PII in prose, a verbal-only high-risk promise) and inbound prompt-injection instead use an **annotator-based** gate at the `output`/`input` point, proven by the remeasure delta since offline `validate` can't run annotators. Never hand-drive an external `acs` CLI for this loop.
- **Config generation is owned by the research workflow** — `research-eval-dimensions.md` runs the isolation preflight, the ≥2-source evidence gate, the `N` deduplicated passes, and the blocking approval step. Do not hand-write a config around it, and do not treat `assert-ai init` as the default path.
- **Never emit an uncited dimension** — every behavior category, stratify dimension, and judge dimension carries inline `# sources:` tags resolving to the config's `# References` block. If live retrieval is unavailable, stop at the ledger and say so rather than shipping remembered citations.
- **Never reuse a built-in judge dimension name** — `policy_violation` and `overrefusal` merge by name into the same dict, so a collision silently replaces the built-in rubric. It does not move the headline split (recomputed from `node_judgments`), but it changes the verdict stored in the run JSON and the default compare metric. `validate_dimension_review.py pre-write` rejects it.
- **Never read a prior generated config to "align" with it** — the isolation preflight is path-only by design. No `read_file`, `cat`, `grep`, `git show`, hashing, or inferring content from size, timestamps, or commit history.
- **One atomic behavior per config** — split N selected risks into N configs run sequentially; never bundle.
- **Triage before running** — never auto-generate an eval for every enumerated risk; ask which to measure now.
- **Don't invent metrics** — only report what's in the artifacts.
- **Don't trawl raw traces to answer questions** — answer from `results status`, `scores.jsonl`, and `metrics.json`; hand off to the viewer for visual trace/transcript exploration.
- **Hand off, don't reimplement the viewer** — for visual drill-down, baseline compare, or live monitoring, point to the local viewer rather than reproducing it in chat.
- **Don't read, print, or commit** `.env`, credential values, `artifacts/`, traces, `.venv`, or logs.
- **Reference env variable NAMES only** (AZURE_API_KEY, AZURE_API_BASE, azure_ad_token, GITHUB_TOKEN, ANTHROPIC_API_KEY) — never values.
- **Don't commit artifacts** to the repository.
