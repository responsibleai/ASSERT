---
name: run-assert-eval
description: >
  Run an ASSERT evaluation against a described risk. Use when the user wants to
  evaluate, test, or check an AI agent, LLM app, or model against
  requirements/policies (e.g. "evaluate my agent for budget violations", "test
  that the support bot never gives legal advice"). Risks come either from
  Clarity — recommended, driving the real Clarity MCP tools (run_clarity) in-IDE
  to discover failure modes the user has not considered — or directly from the
  user as a description, PRD, design doc, threat model, red-team finding, or
  risk assessment. Then researches how that risk has been evaluated in the
  literature and turns it into an evidence-backed, cited config per selected
  risk at examples/<domain>/<risk>/eval_config.yaml, gets it approved, runs
  the pipeline, and reports pass/violation rates with trace-cited failure examples.
---

# Run an ASSERT evaluation

## When to use

The user wants evidence of how their agent or model actually behaves. Not for
fixing the agent — this skill finds and reports failures.

This skill has two entry modes:

- **Run mode** — no usable results exist yet. Establish a **risk source** (Steps 1-2):
  **Clarity** (recommended) — an existing `.clarity-protocol/` directory or a fresh
  discovery run driven through the **Clarity MCP server** (`run_clarity`), in-IDE —
  **or risks the user supplies directly**. Then turn each selected risk into an
  atomic config, run the pipeline (Steps 3-5), and report (Step 6).
- **Results Q&A mode** — judged artifacts already exist under
  `artifacts/results/<suite>/<run>/` and the user asks a *question* about them
  ("what are the highlights?", "top 3 examples of the worst failure mode?", "why
  did case X fail?"). Skip to Step 6 and answer THAT question from the artifacts —
  do not re-run, and do not fall back to the full canned report unless asked.

### Choosing a risk source (Clarity recommended, never required)

Every eval starts from a risk. There are two supported sources, and **the user
chooses** — never decide for them and never block on Clarity.

**Path A — Clarity discovery (recommended — present it first, but never alone).** Use an existing
`.clarity-protocol/` or a fresh run via the Clarity MCP `run_clarity` tool.
Clarity's value is finding failure modes the user has *not* thought of, along
with severity and causal chains. Recommend it whenever the user is unsure what
to measure, is new to the agent, or wants coverage rather than one known bug.

**Path B — user-supplied risks.** The user names the risk themselves, as prose
or by pointing at a PRD, design doc, threat model, red-team finding, incident
report, risk assessment, or test plan. This is the right path when they already
know what they want measured.

**Both paths answer *what* to test for. Neither answers *how*.** That is the job of
the research procedure in Step 3: once a risk is named, it runs a literature review of
**how that risk has actually been evaluated** and turns the findings into the test-set
design. The output is not a restatement of the topic — it is how the topic *manifests*:

- **Timescale.** Psychosocial and relational harms are typically observed across
  turns, not in one answer, so the literature drives `scenario` over `prompt`.
  `max_turns` itself is fixed at `6` — the timescale finding chooses the test
  *mode*, not the turn budget.
- **Viewpoint.** A hospital helpdesk is exercised by its primary users — patients,
  nurses, schedulers — not solely by one adversarial persona. Population and role
  become stratification dimensions when the evidence says they change the harm.
- **Conditions.** Pressure, severity, context position, and trajectory stage become
  explicit `levels` when sources support them.

This is the difference between a config that names a risk and a config that can
actually measure it.

**Whenever you need a new risk to measure**, and the user has not already named
one, **offer the choice**:

> I can find a risk two ways. **Clarity** interviews you and surfaces failure
> modes you may not have considered — recommended when you know the agent but
> aren't sure what to measure. Or **you name it directly**, in your own words or
> by pointing me at a PRD, design doc, threat model, red-team finding, or risk
> assessment — best when you already know what you want measured. Either way I
> then research how that risk has been evaluated and build the test set from
> that evidence. Which do you prefer?

An existing `.clarity-protocol/` changes the **default**, never the **choice**.
Offer it as the recommended option ("I found an existing Clarity protocol with
these risks — measure one of those, or is there a different risk you have in
mind?"), then take the user's answer.

Rules that hold on both paths:

- **An explicit user-supplied risk always wins.** If the user names a risk — in
  prose, or by pointing at a document — measure *that*, whether or not a
  `.clarity-protocol/` exists. Never substitute the protocol's risks for one the
  user just stated. If you think the protocol covers the same ground, say so and
  let them decide; do not decide for them.
- **Never silently pick a path**, and never stall the user on Clarity setup. If
  the Clarity MCP tools are missing and the user wants Path A, offer
  `SETUP-CHECKLIST.md` — but if they'd rather not set it up now, take Path B.
- **Do not imitate Clarity's interview from your own head.** This is the real
  prohibition: if the user picked Path A, drive the actual `run_clarity` tool,
  which returns Clarity's genuine process guide inlined. Path B is not a
  degraded impression of Clarity — it is a distinct, structured intake (Step 1b).
- **Path B meets the same quality bar.** One atomic behavior per config,
  researched and cited stratify dimensions, pinned systematize/judge models, an
  explicit `sample_size`. Steps 3-6 are risk-source agnostic; nothing about the
  config, run, or report changes.
- **Offer Clarity again later.** Declining once is not a permanent opt-out —
  after a run, it's a natural next step for finding what they *didn't* think to
  measure.
- **Clarity write-backs degrade to no-ops.** `record_failure` /
  `record_suggestion` apply only when a protocol exists. On Path B, skip them and
  say so once; never treat their absence as an error.

### Copilot vs. the local viewer

Copilot is for *answering questions* and *synthesis* — direct answers,
failure-mode clustering, cited examples, next actions — with no clicking. The
bundled local viewer is for *visual exploration* — forest plots, baseline compare,
facet grouping, and stepping through a transcript with the judge's citations
highlighted. Answer in chat when the user asks "what / why / which"; hand off to
the viewer (Step 7) when they want to *see*, *read a full transcript*, *compare
runs*, or *watch a live run*.

## Preconditions (check, don't assume)

1. **ASSERT installed**: `assert-ai --help` succeeds. If not, guide install from
   PyPI — not an editable install of the user's own repo:
   ```
   python -m pip install "assert-ai[otel]"
   ```
   Add route-specific extras as needed, for example `assert-ai[otel,langgraph]`
   for LangGraph. `target.endpoint` needs `aiohttp`, which ships transitively via
   `litellm`'s own dependency — no separate extra to install. Use
   `pip install -e ".[otel,langgraph]"` **only** when the working directory is a
   clone of the ASSERT repo itself; inside a customer repo it installs the wrong
   package.

2. **Clarity MCP server available** (needed only for Path A): the `clarity-agent`
   MCP tools (`run_clarity`, `write_protocol_document`, `record_failure`,
   `record_suggestion`, …) are callable in this session. Clarity is the
   risk-discovery engine — the skill drives its real MCP tools, it does not
   reimplement it. If the tools are missing, the server is not wired up yet: offer
   `SETUP-CHECKLIST.md` (install `clarity-agent` with the `[mcp]` extra, run
   `clarity embed .` to generate `.vscode/mcp.json`, reload MCP servers) and
   confirm the LLM provider is configured (`clarity doctor` — Clarity supports
   GitHub Copilot, Anthropic, OpenAI, Azure AI, and Gemini).

   **This is not a blocker.** If the tools can't be made available, or the user
   would rather not set them up now, say so plainly and continue on Path B
   (Step 1b). Never strand the user on MCP setup when they came to measure
   something.

3. **Provider creds exist** in `.env`. NEVER read or print `.env`. If a run fails
   with an auth error, tell the user which variable NAMES are required
   (AZURE_API_KEY, AZURE_API_BASE, OPENAI_API_KEY, GITHUB_TOKEN, ANTHROPIC_API_KEY,
   etc.) — never their values.

## Steps

### 1. Establish the risk source

Ask which path the user wants (see "Choosing a risk source" above), then follow
**1a** or **1b**.

- **The user already named a risk** (prose, PRD, design doc, threat model,
  incident report, test plan) → that is an explicit Path B choice. Go to **1b**,
  even if a `.clarity-protocol/` exists. Do not silently switch to the
  protocol's risks.
- **Intent is ambiguous and a `.clarity-protocol/` exists** → offer it as the
  default and say what's in it, but still ask before selecting it: *"I found an
  existing Clarity protocol covering X and Y — want to measure one of those, or
  is there a different risk you have in mind?"*
- **Intent is ambiguous and no protocol exists** → offer the choice as written
  above.

#### 1a. Clarity discovery (recommended)

Risks come from Clarity's real engine, driven through the **Clarity MCP server** —
never by imitating Clarity's interview from your own head.

- **If a `.clarity-protocol/` directory already exists** in the workspace, and the
  user has chosen Path A for this risk, use it directly as the risk source — skip
  straight to reading its output below. (Selecting Path A is the user's decision,
  made in Step 1; the protocol's presence alone does not make it.)
- **Otherwise run discovery via the Clarity MCP tools:**
  1. Call **`run_clarity`**. It returns Clarity's real process guide inlined as text.
  2. Follow that guide to ask the user the clarifying questions **in chat** — this
     is Clarity's genuine multi-perspective flow, surfaced through you as the host
     agent (Copilot agent mode supports MCP *tools*, so drive the loop yourself
     rather than expecting a separate chat UI).
  3. Persist what you learn with **`write_protocol_document`** and
     **`record_failure`**. Continue until the failure-analysis process has written
     `.clarity-protocol/failures/failures.md`.

Read Clarity's output to enumerate risks:

- **`.clarity-protocol/failures/failures.md`** — the failure modes, causal chains,
  and management plans. Each distinct failure mode is one candidate ASSERT behavior.
- **`.clarity-protocol/summary.md`, `goal/requirements.md`, `solution/architecture.md`**
  — target/context for the eval's `context` field.

**For the full measurement path** — parse → triage → one atomic config per selected
failure → sequential runs → report → close the loop → curate the example — follow
`workflows/measure-clarity-failures.md`. Use the intake parser
(`clarity_intake.py`) to convert `failures.md` into candidate behaviors with
severity→priority mapping and variant-derived stratify dimensions.

> **Before a *fresh* discovery run, check the preservation gate.** `.clarity-protocol/`
> is gitignored, single-domain scratch; `run_clarity` **overwrites** it, destroying
> the prior domain's `failures/`, `goal/`, and `solution/` with no git recovery.
> If a protocol from another domain is present, STOP and let the user export it
> to a user-owned location or explicitly discard it. Never commit the raw
> discovery workspace into `examples/`.

Clarity records severity/management-plan signal (the parser maps Critical→P1,
High→P2, Medium→P3, ranges→max). Order and annotate by what Clarity actually
captured; do not fabricate priorities.

#### 1b. User-supplied risks

The user already knows what to measure. Your job is to turn their input into the
**same candidate-behavior shape** `clarity_intake.py` produces on Path A —
`{name, description, severity, priority, source_doc, candidate_dimensions,
multi_behavior, suggested_splits}` — so Steps 2-6 are identical either way.

1. **Take the input as given.** If they point at a PRD, design doc, threat model,
   incident report, or test plan, read it and extract candidate risks from it.
   Quote what you extracted so they can correct you. If they describe it in prose,
   start from their words — do not paraphrase the risk into something broader.
2. **Ask only what the config actually needs**, in one batch, and skip anything
   already answered by their input or the repo:
   - *What does the agent do, and who uses it?* → `context`
   - *What must it never do?* → `behavior.name` + `behavior.description`
   - *What is it explicitly allowed to do that looks similar?* → the permissible
     boundary. **Do not skip this**: without it the judge cannot separate real harm
     from over-refusal, and both rates become uninterpretable.
   - *How would a user trigger this — innocently, and deliberately?* →
     a candidate `elicitation_variant` stratify dimension — a research *seed*
     for Step 3, not the final set
   - *How bad is it when it happens?* → severity → `priority`
3. **Enforce atomicity now.** If their description bundles several independently
   testable behaviors ("it leaks data and hallucinates prices"), say so and propose
   the split — one config each. This is the `multi_behavior` / `suggested_splits`
   check, applied by hand.
4. **Play it back for confirmation** as an explicit candidate list before
   generating anything, exactly as triage does on Path A.

Set `source_doc` to the file you read, or `user-described` when it came from chat.
Record severity as the user rated it; do not invent a priority they didn't give.

**For the full measurement path** — triage → one atomic config per selected risk →
sequential runs → report → curate the example — follow
`workflows/measure-clarity-failures.md`, the same workflow Path A uses. **Skip its
Step 1 (Parse)**: there is no `failures.md` to parse, so join at Step 2 with the
candidate list you just built. Skip its Step 8 (close the loop in Clarity) too,
unless a `.clarity-protocol/` exists.

Then continue to Step 2. Everything downstream is unchanged.

### 2. Triage — choose which risks to measure now

Clarity intentionally over-produces (whole-lifecycle threat modeling). Do NOT
auto-generate an eval for every failure mode. Surface the enumerated list (ordered
by severity signal) and ask the user which to measure now (e.g.
"top-severity only?", or named picks). Carry only the selected risks forward.

On Path B the list is usually short and already chosen — still play it back and
confirm scope before generating configs, rather than assuming every risk they
mentioned should be measured in this pass.

### 3. Turn each selected risk into an atomic config

ASSERT performs best with **one atomic behavior per eval**. Never bundle multiple
risks into one config — bundling makes `policy_violation` a fuzzy logical-OR and
hides per-behavior signal.

- **1 selected risk** → generate one config and run once.
- **N selected risks** → generate N configs and run them sequentially, one per behavior.

Configs are **researched, cited, and user-approved** — not scaffolded and hoped for.
Follow [`workflows/research-eval-dimensions.md`](workflows/research-eval-dimensions.md) for
each selected risk. That workflow owns the whole of config generation; do not hand-roll a
config here and do not skip its gates.

Its purpose is narrow and worth stating plainly: the risk already has a name by the
time you arrive here. What the research supplies is **how that risk has been evaluated**
— the timescale it becomes observable on, whose viewpoint exercises it, and which
conditions change it — expressed as `stratify` dimensions, `behavior_category_count`,
judge dimensions, and the `scenario` vs `prompt` test mode. It does not re-open
*what* to measure.

Collect one input before entering it, and **never silently default it**:

| Input | Rule |
|---|---|
| `N` | Positive integer — how many complete dimension-generation passes to run before deduplication. Ask for it when missing or invalid rather than inferring one. |

What that workflow does, in order:

1. **Reuse a repo spec first** — `assert-ai library list` / `show <name>`; prefer
   `behavior.preset` or a copy-in spec from `examples/behavior_specs/` over reinventing a
   description. This is also what settles the harm's stable slug.
2. **Isolation preflight** ([`generation-isolation-workflow.md`](workflows/generation-isolation-workflow.md))
   — once the slug is stable, detects prior generations for it **by path only**, and asks
   before using a new dated directory. It never reads a prior generated YAML.
3. **Research the dimension model** — classify the harm's observability, build a dimension
   ledger, and gate each dimension on at least two independent authoritative sources (or
   one plus the repo spec). Behavior categories, stratify dimensions, and judge dimensions
   are researched as three separate namespaces.
4. **Run `N` passes and deduplicate** ([`iterative-dimension-workflow.md`](workflows/iterative-dimension-workflow.md))
   — `N` complete passes, then semantic deduplication within each namespace.
5. **Review and approve** — a compact table per namespace, and an explicit user approval.
   **Silence is not approval**, and the pre-write gate enforces this mechanically.
6. **Write the cited config** to `examples/<domain>/<risk>[_YYYY-MM-DD]/eval_config.yaml`, with
   inline `# sources:` citations and a consolidated `# References` block.

Outputs land at `examples/<domain>/<risk>[_YYYY-MM-DD]/eval_config.yaml` — one directory per
generation, never overwritten. Prefix the eval **suite name** with a domain slug
(`<domain>-<risk>`) so `artifacts/results/<suite>/` and `artifacts/acs/<suite>/` do not
collide across domains.

Two things that workflow will ask you to decide, and that matter downstream:

- **`behavior_category_count` is `25`** — the standard count, and ASSERT's own default
  (`DEFAULT_BEHAVIOR_CATEGORY_COUNT`). Research shapes *which* categories are generated,
  not how many.
- **`sample_size` is a question for the user, but it has a hard floor:
  `≥ behavior_category_count` (so `≥25`).** Below the category count some behavior
  categories receive zero cases and are silently unmeasured. Each rate is also
  `violations / sample_size`, so even at the `25` floor one flipped case moves the
  number 4 percentage points, and the swing grows as the sample shrinks. The floor
  protects coverage, not precision — prefer `50`+ when the expected delta is small.
- **`max_turns` is fixed at `6`** — ASSERT's default (`DEFAULT_TESTER_MAX_TURNS`) and
  the config template's value. The research does not move it, and it must be
  **identical in the baseline and governed configs** or the "only ACS differs"
  comparison breaks. A genuinely single-turn harm is expressed by writing `prompt`
  test cases, not by lowering the turn budget — `max_turns` is read only for
  `scenario` cases.

**Judge dimensions are authored** from the research, and are added **on top of** the
built-ins — but **never reuse a built-in name**. `policy_violation` and `overrefusal` are
`BUILT_IN_DIMENSIONS` (`assert_ai/core/judge.py`) and are always judged unless explicitly
disabled. Config dimensions merge over the built-ins **by name** into the same dict, so a
researched dimension called `policy_violation` silently replaces the built-in rubric — no
warning, no error.

**What shadowing does and does not affect.** It does **not** move the headline pair. Both
`not_permissible_policy_violation_rate` and `permissible_policy_violation_rate` are
recomputed in `assert_ai/results.py` from the judge's per-behavior `node_judgments` — each
relevant node's `violated` flag, bucketed by that behavior category's `permissible` value —
so they never read either dimension's verdict. What shadowing *does* change is the
dimension verdict persisted in the run JSON, and `DEFAULT_COMPARE_METRIC`
(`assert_ai/cli.py`), which is still `policy_violation`. That is a comparability and
stored-artifact problem, not a corrupted headline.

The same applies to **judge presets**, which expand into that same merged list:

- **Do not set `judge.preset: safety-core`.** Its only two dimensions are named exactly
  `policy_violation` and `overrefusal`, so it replaces *both* built-in rubrics — and buys
  nothing, because the engine treats that pair as **superseded**:
  `_SUPERSEDED_DISPLAY_METRICS` (`assert_ai/cli.py`) hides both from CLI detail surfaces
  whenever the permissibility split is available, which for this skill's configs is always.
  You would pay judge tokens per dimension per row for two numbers that are never shown and
  never reported.
- **`safety-extended` is safe and recommended** for nuanced harms — it defines
  `harm_actionability` and `pii_leakage`, which collide with nothing and are displayed.

The post-write gate rejects both the inline and the preset form.

**If live source retrieval is unavailable**, say so and stop at the ledger. The evidence
gate cannot be met without it, and a config with remembered or invented citations is worse
than no config. `assert-ai init --describe-file …` remains available as an explicitly
unvalidated scaffold — never for a measurement you intend to report or govern against.

After generation, show the user the resolved `behavior.description`, `context`,
`pipeline.judge` settings, the `systematize` / `judge` models, and the reference list.
Confirm before running.

### 4. Identify the target shape

Help the user set the right target in the config:

- **Framework agent** (LangGraph, CrewAI, etc.) with a Python entry function:
  use `target.callable` WITH `target.trace` so the judge can cite tool calls and routing.
- **Hosted model** with a system prompt and optional tools:
  use `target.model` and `target.tools`.
- **Pre-collected traces** (no live inference needed):
  use `assert-ai judge-traces --traces <path> --config <path>`; do not add a `--trace` flag to `assert-ai run`.
- **Black-box HTTP endpoint** you cannot import as Python:
  use `target.endpoint` — the runtime POSTs `{"message": ..., "history": [...]}` and reads `{"response": ...}`, so no wrapper code is needed (requires `aiohttp`). Only write a thin `target.callable` shim if the service's request/response shape differs. Either way the judge sees only final text, so this is a fallback, not the recommended path.

#### The callable contract — verify before the first run

`target.callable` takes a `module.path:function` reference. The full signature and
return-type contract lives in `docs/targets/callable.md`.
Two behaviors that doc does **not** cover can silently corrupt a run:

- **`history` is detected by parameter *name*, not position.** ASSERT introspects the
  signature and enables multi-turn only when a parameter is literally named `history`.
  Name it `messages`, `conversation`, or `chat_history` and every scenario **silently
  degrades to single-turn** — the run completes, the viewer renders, and the numbers are
  wrong with no warning. Confirm the name before trusting any multi-turn baseline, and
  therefore any ACS delta measured against it.
- **Module resolution has a four-step fallback**: `sys.path` → the **config's own
  directory** → the current working directory → direct file load. An `agent.py` sitting
  beside the YAML config resolves even when the CLI is invoked from the repo root —
  but a same-named module earlier on `sys.path` wins, so prefer a domain-unique module
  name over a bare `agent`.

#### Why `target.trace` is not optional

Tracing decides how much of the agent the judge can actually see. Per the observability
matrix in `docs/targets/callable.md` ("What the judge sees, by integration path"):

| Integration path | Signals visible to the judge |
|---|---|
| Plain `str` return | 1 of 8 — final text only |
| LiteLLM-style response | 4 of 8 — adds *final* tool calls, token usage, model name |
| **OTel traces** | **8 of 8** — adds *intermediate* tool calls, routing / sub-agent decisions, intermediate model calls, per-span latency |

So without traces a tool-misuse or wrong-routing failure is largely invisible to scoring —
which is why this skill mandates `target.callable` **with** `target.trace`. You rarely
hand-write spans: ASSERT ships OTel auto-instrumentation for 33 frameworks (LangChain /
LangGraph, CrewAI, OpenAI Agents SDK, DSPy, LlamaIndex, AutoGen, MAF, Pydantic AI, …) as a
single helper call at the top of the callable module — see `docs/targets/callable.md`
("Recommended: OTel-traced agent (33 frameworks)").

### 5. Run the pipeline

**Offer a smoke run first.** A suite is 25 prompt + 25 scenario cases, and
plumbing errors (wrong `callable`, missing credentials, a callable that raises
on its first tool call, tool-schema mismatch, undeployed judge model) surface
only once inference starts. Validate on 3 real cases first:

```
# 1. artifacts only, no inference cost
assert-ai run --config examples/<domain>/<risk>/eval_config.yaml \
  --override inference.enabled=false --override judge.enabled=false

# 2. slice 3 real rows out of the generated test set
python .claude/skills/run-assert-eval/smoke_slice.py \
  --config examples/<domain>/<risk>/eval_config.yaml --count 3

# 3. inference + judge on those rows only
assert-ai run --config examples/<domain>/<risk>/eval_config.yaml \
  --override run=<run>-smoke \
  --override inference.test_set_path=<out path from step 2>
```

If it fails, stop and report — do not start the full run. Three cases is not a
measurement, so never report a rate from a smoke run. Never lower
`test_set.sample_size` instead: it invalidates the cached test set and does not
produce a subset. Full detail in `workflows/measure-clarity-failures.md`
Step 5a.

```
assert-ai run --config examples/<domain>/<risk>/eval_config.yaml --output json
```

This is long-running (systematize -> test_set -> inference -> judge). Stream status
to the user as each stage completes. For N configs, run them sequentially and track
each `suite`/`run`. After a smoke run the first two stages report CACHED.

- To re-run from a specific stage: `--force-stage <stage>`
- Note the `suite` and `run` names from the config for Step 6.

### 6. Report results — never collapse to one number

**Read only structured artifacts.** Aggregate from the pre-computed, schema'd files —
never trawl raw Phoenix/OpenTelemetry traces to reconstruct an answer (that bulk,
unguided trace-reading is exactly what the viewer's evidence drawer is for). Reading
the `inference_set.jsonl` row for a *specific case the judge already cited* is fine;
bulk trace trawling is not.

1. **Headline rates**: run `assert-ai results status <suite> <run>` for per-dimension
   flagged rates (split into prompt and scenario). Report the violation dimension and
   `overrefusal` SEPARATELY — they are two different problems. Note: the built-in
   `policy_violation` ORs over ALL violated taxonomy nodes (permissible included), so
   it couples with `overrefusal`. The headline pair is the permissibility split: add
   `--json` and read `not_permissible_policy_violation_rate` (real harm got through)
   and `permissible_policy_violation_rate` (the agent broke a behavior it was allowed
   to do), each one vote per conversation. Those are the two numbers to headline in an
   ACS A/B — harm should drop while permissible stays flat (see
   `workflows/govern-and-remeasure.md`). The viewer exposes the same pair as the
   dimension keys `policy_violation_not_permissible` / `policy_violation_permissible`,
   rendered on screen as **Impermissible behavior violated** /
   **Permissible behavior violated**.

2. **Researched judge dimensions**: configs generated by
   `workflows/research-eval-dimensions.md` carry harm-specific judge dimensions on top of
   the built-ins (e.g. `harm_actionability`, `severe_harm_escalation`,
   `longitudinal_harm_pattern`). Report each one's flagged rate **alongside** the
   permissibility split, never folded into it — they answer different questions
   ("did harm get through?" vs "how bad was it when it did?"). Name each dimension as the
   config defines it, and quote its `rubric` when the rate needs interpreting.

3. **Top failing cases**: read `scores.jsonl` from `artifacts/results/<suite>/<run>/`.
   For each dimension with failures, pull 3-5 representative cases with:
   - The test case description (what was tested)
   - `verdict.dimensions` — which dimensions failed
   - `verdict.dimension_justifications` — the judge's rationale with cited evidence
   - `verdict.node_judgments` — which behavior categories were violated, with reasoning

4. **Cost and timing**: read `metrics.json` for token usage and elapsed time per stage.
   This file contains cost metadata only, not score roll-ups.

For **Results Q&A mode**, answer the user's specific question from these same artifacts
(e.g. rank dimensions by flagged rate for "top failure mode", then quote
`dimension_justifications` for the cited examples). Don't emit the full template unless asked.

### 7. Hand off to the local viewer

After reporting, point the user to the bundled viewer for anything visual or
self-directed — it went through extensive design iteration and owns the exploration
surface Copilot should not replicate:

```
cd viewer && npm install && npm run dev   # then open http://localhost:5174
```

Select the suite and run for forest plots, per-dimension breakdowns, facet grouping,
the permissible vs. not-permissible policy-violation split (also available from
`assert-ai results status --json` and rendered by `results compare`),
and a transcript drawer with the judge's `[N]` citations highlighted on the cited turns.
Suggest it specifically when the user wants to:

- **read a full transcript** or **see the trace** for a case → viewer evidence drawer
- **compare against a baseline** → viewer compare view (or `assert-ai results compare <suite> <runA> <runB>`)
- **watch a run in progress** → viewer live run monitor (`manifest.json`-driven)

See `docs/guides/use-local-viewer.md` for the full layout.

### 8. Govern the failure and re-measure (ACS)

When a run surfaces `policy_violation` failures and the user wants to **fix and
prove it**, don't stop at prompt-tweaking. Generate a deployable **ACS** (Agent
Control Specification) policy from the findings and re-run the same eval against
the governed agent to show the failure rate dropped — the ACS delta. This uses
ASSERT's native `assert-ai acs generate` / `validate` adapter (no external `acs`
CLI). It requires a **callable** target whose high-risk tools can be wrapped
(`control.protect_tool`); a hosted-model Prompt Agent target has nothing
wrappable. Follow `workflows/govern-and-remeasure.md` for the full loop
(baseline → `acs generate` → `acs validate` → governed run → delta from two
`results status --json` calls → export each run to standalone HTML → close the
loop in Clarity). `results compare --metric policy_violation_not_permissible`
and `--metric policy_violation_permissible` compare either half directly; use
the two `status --json` rate fields when you need machine-readable counts.
**Classify the failure before generating the policy** (Step 1a): read the baseline's
`verdict.dimension_justifications` to decide semantic (`output` annotator) vs
structural (tool gate), and confirm the harm actually routes through the tool you
plan to gate. Getting that wrong is the main cause of a gate that fires ~0 times.
If the governed run's delta still comes out wrong (no drop, or `overrefusal` rose),
`workflows/diagnose-acs-delta.md` is the symptom-indexed diagnostic manual —
match the signature, apply the smallest fix, cap at ~4 attempts.
`examples/billing_support_agent/agent.py` shows the baseline callable shape; the
governed entrypoint is an output of that workflow, not a checked-in file.

### 9. Hand off to CI

Once the eval is passing locally and the user wants it enforced on PRs, hand off to the `wire-assert-ci` skill. Do not author CI wiring here; direct the user or agent to the action bootstrap:

```
read https://raw.githubusercontent.com/responsibleai/assert-ai-action/main/ONBOARD.md
```

## Output format

Present a short summary with this structure:

**Headline metrics**:
- Impermissible behavior violated: X% (N/M cases) [`not_permissible_policy_violation_rate`]
- Permissible behavior violated: X% (N/M cases) [`permissible_policy_violation_rate`]
- Overrefusal rate: X% (N/M cases) — the separate availability check

Report the permissibility split as the headline pair (from `results status --json`);
the raw `policy_violation` rate ORs over all violated nodes and couples the two, so
quote it only as context, never as the headline.

**Researched judge dimensions** (when the config declares them), each on its own line
with its flagged rate — reported beside the headline pair, never merged into it:
- `<dimension_name>`: X% (N/M cases) — one-line gloss of what its rubric scores

**Evidence base**: the config's `# References` list (tag → title → URL), so the
provenance of the dimensions being reported is visible alongside the numbers.

**Top failing cases** (3-5 per dimension):
For each failure:
- Requirement cited: [behavior category from taxonomy]
- Action cited: [specific turn or tool call from judge rationale]
- Judge rationale: [verbatim from dimension_justifications]

**Suggested next step**: one concrete action (e.g. "tighten the system prompt
around X behavior", "add a stratify dimension for Y", or **govern the failure with ACS and
re-measure to prove the rate dropped** — see Step 8 and
`workflows/govern-and-remeasure.md`).

## Authoritative references

Team-maintained docs on `main`. Prefer linking these over restating their content here —
when they disagree with this skill on *product behavior*, they win; this skill owns the
*methodology* (the Clarity → ASSERT → ACS → ASSERT loop) and the traps called out above.

| Doc | Use it for | Step |
|---|---|---|
| `docs/guides/create-evaluation.md` | Authoring an eval config from scratch | 3 |
| `docs/config/schema.md` | Full config field reference | 3 |
| `docs/targets/callable.md` | Callable signature, return types, OTel auto-instrumentation | 4 |
| `docs/targets/model-and-tools.md` | `target.model` + `target.tools` shape | 4 |
| `docs/guides/troubleshooting.md` | A run errors, hangs, or produces no scores | 5 |
| `docs/guides/results.md` | Interpreting results and artifacts | 6 |
| `docs/guides/use-local-viewer.md` | Viewer layout and drill-down | 7 |
| `docs/guides/securing-agents-with-acs.md` | The ACS generate → validate → guard → re-run path | 8 |

## Bundled workflows

| Workflow | Owns |
|---|---|
| `workflows/measure-clarity-failures.md` | The full measurement path: parse → triage → config → run → report → close the loop |
| `workflows/research-eval-dimensions.md` | **Config generation** — evidence-gated dimension research, `N` passes, approval, cited write |
| `workflows/iterative-dimension-workflow.md` | The `N`-pass cycle, semantic deduplication, and the approval gate |
| `workflows/generation-isolation-workflow.md` | Path-only prior-generation preflight and isolated output directories |
| `workflows/evaluation-intent-workflow.md` | Optional intake: what decision the eval supports, and for whom |
| `workflows/govern-and-remeasure.md` | The ACS baseline → generate → governed run → delta loop |
| `workflows/diagnose-acs-delta.md` | Symptom-indexed diagnostics when the ACS delta comes out wrong |

Helper scripts at the skill root: `clarity_intake.py` (parse `failures.md`),
`smoke_slice.py` (slice N real rows for a smoke run), `plan_generation_path.py`
(path-only isolation preflight), `validate_dimension_review.py`
(render / validate / pre-write / post-write gates).

## Guardrails

- **Clarity is the recommended risk source, not a gate** — present **both**
  options together whenever the user needs a new risk: Clarity discovery
  (existing `.clarity-protocol/` or a fresh `run_clarity` run) *and* risks they
  supply themselves. Recommend Clarity, because it surfaces failure modes they
  haven't considered — but never present it as the only route. Any menu, list, or
  question you offer that includes a Clarity option must carry the user-supplied
  option beside it; a user who doesn't know Path B exists cannot ask for it. Hold
  the user-supplied path (Step 1b) to the same bar: atomic behaviors, an explicit
  permissible boundary, researched and cited dimensions. Never block a measurement on
  Clarity setup.
- **The skill does not invent risks** — risk *identification* is Clarity's job, or
  the user's (red team, threat model, risk assessment). Step 3's research answers
  *how to measure* a risk that already has a name; it never substitutes for
  deciding *what* to measure. If the user has no risk and no protocol, offer
  Clarity — do not silently generate a harm list of your own.
- **Never imitate Clarity's interview from your own head** — if the user chose
  Clarity, drive the real MCP tools (`run_clarity` returns its genuine process
  guide inlined). Step 1b is a distinct structured intake, not a hand-rolled
  impression of Clarity.
- **Drive the real Clarity MCP tools in-IDE** — use `run_clarity` / `write_protocol_document` / `record_failure` for discovery and `record_suggestion` to close the loop; never hand the user off to a separate Clarity app and never shell out to a `clarity cli` process.
- **Close the loop when a protocol exists** — after a run, offer `record_suggestion` (or `record_decision`) back into `.clarity-protocol/` noting the failure mode now has a measured baseline and where the eval lives, so Clarity's staleness tracking stays aware of it. With no protocol, skip it silently — and consider offering Clarity as a next step for finding risks this pass didn't cover.
- **Govern with ACS, don't just prompt-tweak** — to fix and *prove* it, generate an ACS policy from the findings (`assert-ai acs generate`), review it (scope the gated tools, tighten conditions), and re-run the same eval against the governed callable to show the delta; needs a wrappable callable target (`workflows/govern-and-remeasure.md`). Generated policies, guarded targets, and governed configs are local run output by default. Commit them only in the user's own product repo when the user wants a reviewed policy deployed; do not automatically add them to ASSERT's worked examples. Whenever a gate needs a value the model doesn't put in the tool args — a trusted session flag (verification), a trusted comparison value (the caller's own id), a trusted numeric cap, or a running total / prior-call fact — the governed agent must surface that scalar from its **session state** into the tool-call **policy_target** so the generated `input.policy_target.value.*` rule actually fires. ACS evaluates each call in isolation, so multi-call constraints (running totals, ordering, rate limits) are handled by that same injection, not by encoding history in Rego. Free-form content failures (unsafe advice, PII in prose, a verbal-only high-risk promise) and inbound prompt-injection instead use an **annotator-based** gate at the `output`/`input` point, proven by the remeasure delta since offline `validate` can't run annotators. Never hand-drive an external `acs` CLI for this loop.
- **Organize by domain across runs** — prefix every eval **suite name** with a domain slug (`<domain>-<risk>`, e.g. `billing-cross-customer-data-exposure`, `science-<risk>`), so `artifacts/results/<suite>/` and `artifacts/acs/<suite>/` do not collide. Treat `.clarity-protocol/` as uncommitted single-domain scratch; preserve it outside `examples/` only when the user asks.
- **Per-example package** — every worked example must be a small, self-contained folder under `examples/<domain>/` containing only what a customer needs to understand and reproduce the ASSERT run:
  - `agent.py` (+ any real runtime deps it imports, e.g. `tools.py` / `mock_tools.py`) — the runnable baseline.
  - `README.md` — scenario, setup, atomic behaviors, run commands, and result paths.
  - `eval_config.yaml` — one independently runnable baseline config per behavior, written by
    `workflows/research-eval-dimensions.md` into its own isolated
    `examples/<domain>/<risk>[_YYYY-MM-DD]/` directory and never overwritten.
  Do not commit the dimension-review ledger or its approval stamp — those are working
  artifacts under `artifacts/dimension-reviews/`.
  A deliberately curated ACS demonstration may additionally keep the smallest
  reviewed policy, guarded target, and governed config needed to reproduce its
  claim, but ordinary worked examples must not accumulate generated governance
  output. Do not commit generated taxonomies, test sets, result artifacts,
  discovery mailboxes, snapshots, protocol archives, or automatic skill output.
- **One atomic behavior per config** — split N selected risks into N configs run sequentially; never bundle.
- **Generate configs through the research workflow, not by hand** — `workflows/research-eval-dimensions.md` owns config generation. Every dimension must pass its evidence gate, `N` passes must complete, and the user must explicitly approve the dimension set before any YAML is written. **Silence is not approval.** `assert-ai init` remains available as an explicitly unvalidated scaffold, never for a measurement you intend to report or govern against.
- **Never emit an uncited config** — cite only pages actually retrieved this session; never fabricate or guess a URL, title, or author. Keep unsourced candidates in the ledger as `uncited — needs review`. If live retrieval is unavailable, stop at the ledger and say so.
- **Never reuse a built-in judge dimension name** — `policy_violation` and `overrefusal` are `BUILT_IN_DIMENSIONS`; config dimensions merge over them by name, so reusing one silently replaces its rubric. This does **not** move the headline split (`results.py` recomputes it from `node_judgments`), but it does change the verdict stored in the run JSON and `DEFAULT_COMPARE_METRIC`. `judge.preset: safety-core` does this too, since presets expand into the same merged list — and buys nothing, because the engine treats that pair as superseded and hides it once the permissibility split is available. The pre-write gate rejects a reused name in the review ledger; the post-write gate rejects it in the written config, in both the inline and the preset form.
- **Never read a prior generated config** — the isolation preflight discovers prior generations **by path only**. Do not `cat`, parse, grep, hash, `git show`, or otherwise inspect a matching prior YAML, and do not infer its contents from size, timestamps, or commit history.
- **Triage before running** — never auto-generate an eval for every enumerated risk; ask which to measure now.
- **Don't invent metrics** — only report what's in the artifacts.
- **Don't trawl raw traces to answer questions** — answer from `results status`, `scores.jsonl`, and `metrics.json`; hand off to the viewer for visual trace/transcript exploration.
- **Hand off, don't reimplement the viewer** — for visual drill-down, baseline compare, or live monitoring, point to the local viewer rather than reproducing it in chat.
- **Don't read, print, or commit** `.env`, credential values, `artifacts/`, traces, `.venv`, or logs.
- **Reference env variable NAMES only** (AZURE_API_KEY, AZURE_API_BASE, azure_ad_token, GITHUB_TOKEN, ANTHROPIC_API_KEY) — never values.
- **Don't commit artifacts** to the repository.
