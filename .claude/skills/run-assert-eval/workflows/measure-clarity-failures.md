# Workflow: measure-clarity-failures

Turn discovered or user-supplied failure modes into measured ASSERT violation
rates — one atomic behavior at a time, with a human in the loop at every gate.

This workflow is the measurement half of the Clarity → ASSERT story. Discovery
is owned by the **Clarity MCP server** (`clarity-agent`, shipped by
microsoft/clarity-agent); measurement is owned by this skill. The handoff is
**files, not JSON**: Clarity writes `.clarity-protocol/failures/`, and this
workflow reads it.

Clarity is the **recommended** risk source, not a required one. When the user
supplies risks directly (SKILL.md Step 1b), skip Step 1 (Parse) and start at
Step 2 (Triage) — Steps 2-9 never touch `.clarity-protocol/` except at the
optional close-the-loop step.

> **Discovery is agent-driven, not scripted.** The Clarity MCP `run_clarity`
> tool returns the relevant process guide inlined as text; **you** (the host
> agent) ask the user the clarifying questions in chat and persist what you learn
> with `write_protocol_document` / `record_failure`. Do not reimplement Clarity's
> questioning and do not shell out to a separate app — drive its real MCP tools.

## Entry conditions

Trigger this workflow when the user asks to **measure / test / quantify** risks
or failures for their agent, model, or app.

1. **If the user already named a risk** (prose, PRD, design doc, threat model,
   incident report, test plan) → that is an explicit user-supplied choice.
   Follow **SKILL.md Step 1b**, then **skip Step 1 (Parse)** and join at **Step 2
   (Triage)**. Do this even when `.clarity-protocol/failures/failures.md` exists —
   never substitute the protocol's risks for the one the user just stated.
2. **Otherwise, if `.clarity-protocol/failures/failures.md` exists** → offer it as
   the default, naming what it covers, and ask before selecting it: *"I found an
   existing Clarity protocol covering X and Y — want to measure one of those, or is
   there a different risk you have in mind?"* On confirmation, go to
   **Step 1 (Parse)**. If they name something else instead, treat it as case 1.
3. **If no protocol exists** → ask which risk source the user wants (see SKILL.md,
   "Choosing a risk source"). Recommend Clarity, but take their answer:
   - **Clarity discovery (recommended)** —
     **run the preservation gate below first**; a fresh discovery run destroys
     any protocol from a previous domain. Call the Clarity MCP tool
     **`run_clarity`** and follow the inlined process guide's clarifying questions
     *with the user in chat*. Persist findings via **`write_protocol_document`**
     and **`record_failure`**. Continue until the failure-analysis process has
     produced `failures/failures.md`, then proceed to Step 1.
   - **User-supplied risks** — the user names the risk, or points at a PRD,
     design doc, threat model, incident report, or test plan. Follow **SKILL.md
     Step 1b** to build the candidate-behavior list by hand, then **skip Step 1
     (Parse)** — there is no `failures.md` to parse — and join at **Step 2
     (Triage)**. Everything from Step 2 onward is risk-source agnostic.
   - If the `clarity-agent` MCP tools are **not available** in this session, say
     so, offer the in-IDE setup checklist (`SETUP-CHECKLIST.md`: `clarity embed`,
     reload MCP servers, confirm `run_clarity` is callable), and let the user
     choose. If they'd rather not set it up now, continue with user-supplied
     risks — do not strand them on MCP setup.

### Preservation gate (blocking — check before any fresh `run_clarity`)

`.clarity-protocol/` is a single, non-namespaced scratch directory at the repo
root, and it is **gitignored**. A fresh discovery run **overwrites** the prior
domain's `failures/`, `goal/`, and `solution/` — and because the directory was
never committed, that content is **unrecoverable**.

Before calling `run_clarity` for a *new* agent/domain:

1. **Check** whether `.clarity-protocol/` exists and is non-empty.
2. **If it does, STOP.** Do not call `run_clarity`. Tell the user which domain
   the existing protocol belongs to and offer to export it to a user-owned
   location outside `examples/`.
3. Proceed only after the user has preserved it or explicitly said to discard
   it. Do not commit the raw protocol, mailboxes, snapshots, or transcripts to
   this repository.

Skip this gate only when `.clarity-protocol/` is absent or empty. Clarity
re-scaffolds a clean one on the next `run_clarity`.

## Step 1 — Parse

**Clarity-sourced risks only.** If the user supplied risks directly (SKILL.md
Step 1b), there is no `failures.md` to parse — skip to Step 2 with the candidate
list you built there.

Run the intake parser (`clarity_intake.py`) on the protocol directory:

```
python .claude/skills/run-assert-eval/clarity_intake.py .clarity-protocol
```

It emits, per failure mode, a **candidate behavior**:
`{name, description, severity, priority, source_doc, candidate_dimensions,
multi_behavior, suggested_splits, warnings}`.

- `priority` maps `Critical→P1`, `High→P2`, `Medium→P3`, `Low→P4`. Severity
  **ranges collapse to the maximum** (e.g. `Medium–Critical → Critical → P1`).
- `candidate_dimensions` are mined from the doc's **Variants** (highest-value:
  the `elicitation_variant` dimension) and **Failure Chain** conditions
  (`interaction_condition`).
- `multi_behavior: true` + `suggested_splits` flags a doc that **bundles** several
  independently testable behaviors (see Step 4 atomicity).
- The JSON is a **disposable cache** — `.clarity-protocol/` remains the source of
  truth. Never treat the JSON as authoritative or commit it as such.

Parsing is tolerant: docs with unknown severity labels or missing headers arrive
**flagged** (`warnings` populated), never dropped. Surface those warnings during
triage so the user knows what needs manual attention.

## Step 2 — Mandatory human triage gate (never skip)

Clarity **intentionally over-produces** (whole-lifecycle threat modeling).
Auto-running every risk is a bug, not a feature.

Present the candidate list **sorted P1 → P3**, each row showing:

- name, priority, one-line summary
- any atomicity split (`suggested_splits`)
- any parse warnings

Ask the user **which to measure now**. Offer **"P1s only"** as the default
suggestion, plus named picks. **Do not generate or run anything until the user
answers.** Declining at this gate must result in **zero files written and zero
runs**.

## Step 3 — Confirm scope, then generate one config per selected behavior

For **each** selected behavior, produce its **own** config in its own isolated
directory: `examples/<domain>/<risk>[_YYYY-MM-DD]/eval_config.yaml`. Use a clear snake_case
slug. Never bundle.

**Config generation is owned by
[`research-eval-dimensions.md`](research-eval-dimensions.md).** Follow it per selected
behavior; do not hand-roll a config here and do not skip its gates. It reuses a repo
behavior preset where one matches (settling the harm's stable slug), runs the path-only
isolation preflight on that slug, researches **how the harm has been evaluated** against
primary sources, runs `N` complete passes, deduplicates, blocks on explicit user approval,
and writes a cited config.

The risk is already named by the time you reach this step — triage settled that. What the
research settles is the test-set design: the timescale the harm becomes observable on,
whose viewpoint the probes are authored from, and which conditions the evidence says change
it.

Collect `N` (positive integer) first, and never silently default it.

Fill from the candidate behavior (real schema field names):

| Config field | Source |
| --- | --- |
| `behavior.name` | candidate `name` (short, specific), or a matching library preset via `behavior.preset` |
| `behavior.description` | candidate `description` (the doc **Summary**, tightened to a *testable* statement), or the preset's curated description |
| `context` | Clarity `summary.md` / `goal/requirements.md` / `solution/architecture.md` |
| `default_model.name` | the cheap model — drives the target, test-set generation, and tester (e.g. `azure/gpt-5.4-mini`) |
| `pipeline.systematize.model` + `pipeline.judge.model` | **pin both to the strong model** (e.g. `azure/gpt-5.4`) — see the ground-truth note below |
| `pipeline.systematize.behavior_category_count` | **`25`** — the standard count, and ASSERT's own default (`DEFAULT_BEHAVIOR_CATEGORY_COUNT`); research shapes *which* categories are generated, not how many |
| `pipeline.systematize.web_search` | `true`, so systematization can expand categories with current context |
| `pipeline.test_set.stratify.dimensions` | the **approved, deduplicated** dimension set — with explicit `levels` where the literature supports them. `candidate_dimensions` from the parser (notably `elicitation_variant`) are *seeds* for research, not the final set |
| `pipeline.test_set.prompt.sample_size` | **ask the user (see the sizing note below)** — do not pick silently, and never accept a value below `behavior_category_count` (so **`≥25`**); recommend `25`, `50`+ for the tightest signal |
| `pipeline.test_set.scenario.sample_size` | same — ask once and apply the user's answer to **both** `prompt` and `scenario` unless they say otherwise; the same `≥ behavior_category_count` floor applies (see `govern-and-remeasure.md`) |
| `pipeline.inference.target` | the target shape (see below) |
| `pipeline.inference.max_turns` | **Fixed `6`** — ASSERT's default (`DEFAULT_TESTER_MAX_TURNS`) and the config template's value. Not research-derived. Use the **same** value in the baseline and governed configs; a genuinely single-turn harm uses `prompt` cases, which ignore this knob. |
| `pipeline.judge.preset` | `safety-extended` for nuanced harms (additive). **Not `safety-core`** — it replaces both built-in rubrics; skip it if a library preset suggests it |
| `pipeline.judge.dimensions` | the **approved researched judge dimensions**, under new names only — never a built-in name (see the built-in note below) |

> **Run the eval cheap, but judge and systematize with the strong model.**
> `assert-ai init` has no `--systematize-model` / `--judge-model` flag, so every
> stage silently inherits `default_model`. Edit the generated config by hand:
>
> ```yaml
> default_model:
>   name: azure/gpt-5.4-mini      # target, test-set, tester
> pipeline:
>   systematize:
>     model: azure/gpt-5.4        # authors the taxonomy
>   judge:
>     model: azure/gpt-5.4        # renders every verdict
> ```
>
> This matches the repo's own `examples/` configs. These two stages define and
> apply ground truth: `systematize` authors the behavior tree and the
> permissible / non-permissible split that every metric is measured against,
> and `judge` decides both applicability and violation for each row on a
> single sample (`judge.n` defaults to `1`, and judge temperature is not
> pinned). Leaving them on the cheap model does not just add noise around a
> fixed target — it moves the target, and it inflates run-to-run drift in
> which rows are even considered applicable. Verify with
> `assert-ai results status <suite> <run> --json` — the model actually used is
> echoed at `prompt_metrics.judge_model` / `scenario_metrics.judge_model`.

> **Author researched judge `dimensions`, but never reuse a built-in name.**
> `policy_violation` and `overrefusal` are `BUILT_IN_DIMENSIONS`
> (`assert_ai/core/judge.py`) and are **always judged** unless explicitly disabled —
> you get both for free. The researched harm-specific dimensions from
> `research-eval-dimensions.md` (e.g. `harm_actionability`, `severe_harm_escalation`,
> `longitudinal_harm_pattern`) are **added on top** of them. Config dimensions are
> merged over the built-ins **by name** into the same dict, so declaring one called
> `policy_violation` or `overrefusal` silently **replaces the built-in rubric** with a
> hand-written one. That does **not** move the headline permissibility split — which
> `assert_ai/results.py` recomputes from the per-behavior `node_judgments`, not from
> either dimension's verdict — but it does change the verdict stored in the run JSON and
> `DEFAULT_COMPARE_METRIC`, so stored results stop meaning what the engine documents. The
> same applies to `judge.preset`, which expands into that same merged list — do **not**
> use `safety-core`, whose only two dimensions are named exactly `policy_violation` and
> `overrefusal`, and which the engine hides anyway once the split is available. The
> validator's pre-write gate rejects a reused name in the ledger, and its post-write gate
> rejects it in the written config, in both the inline and the preset form.

> **Built-in `policy_violation` couples with `overrefusal` — read the split instead.**
> The built-in `policy_violation` dimension is the logical-OR over ALL violated
> taxonomy nodes — including *permissible* ones — so over-gating a permissible
> behavior also trips it, and it can never be fully separate from `overrefusal`.
> For a plain baseline that's usually fine. When you need the decoupled numbers
> (any ACS before/after A/B — see `govern-and-remeasure.md`), don't restructure the
> config: `assert-ai results status <suite> <run> --json` already reports the
> headline pair — `not_permissible_policy_violation_rate` (real harm) and
> `permissible_policy_violation_rate` (allowed behavior broken) — each one vote per
> conversation. The split is recomputed from the stored per-behavior `node_judgments`,
> so it needs no config
> change and works on existing runs. In the viewer the same pair appears as the
> dimension keys `policy_violation_not_permissible` / `policy_violation_permissible`,
> labelled **Impermissible behavior violated** / **Permissible behavior violated**. When the
> split is present the viewer now **hides** `policy_violation` / `overrefusal` as
> superseded — they are still judged, aggregated, and written to artifacts.

> **Sizing floor: `sample_size` must be `≥ behavior_category_count` (so `≥25`).**
> Two independent reasons, and both have to hold.
>
> **Coverage.** The test set spreads `sample_size` cases across
> `behavior_category_count` categories. Below the category count some categories
> receive **zero** cases, so `coverage_at_k(…, k=1, …)`
> (`assert_ai/analysis/test_set_metrics.py`) cannot reach `1.0` — those categories
> still sit in the denominator while never being probed. A harm that lives in an
> unsampled category reads as absent.
>
> **Noise.** Each rate is `violations / sample_size`, so **even at the `25` floor
> one flipped case moves the number 4 percentage points** — and the swing grows as
> the sample shrinks. Inference is non-deterministic (agent temperature is 1.0;
> gpt-5 models can't be pinned lower), so two independent runs of the *same* config
> drift by a case or two purely by chance. That distorts an **ACS before/after A/B**:
> a phantom swing can masquerade as a governance effect (or hide one). This is why
> `50`+ is worth the cost when the expected delta is small — the floor protects
> coverage, not precision.
>
> **Always ask the user for the sample size before generating the config — do not
> pick it silently, and do not accept a value below `behavior_category_count`.**
> Present the tradeoff in one line, e.g.: *"How many cases per behavior should I
> sample? `25` = the floor and the recommendation, `50`+ = tightest signal. Cost
> scales linearly. I'll use the same size for prompt and scenario."* If the user has
> no preference, use `25`. If they ask for less, explain the coverage floor and offer
> `25` — there is no supported sub-coverage "quick look".

> **Leave `pipeline.inference.max_turns` at `6`; do not lower it (e.g. `2`).**
> `max_turns` caps the alternating tester↔target loop for **scenario** (multi-turn)
> cases (single-turn `prompt` cases ignore it). `6` is the ASSERT default
> (`DEFAULT_TESTER_MAX_TURNS`) and gives a realistic persistence/erosion arc room to
> land — many of the strongest findings are **multi-turn erosion** (the agent holds
> firm for a few turns, then softens into a dose/clearance/leak under pressure). A low
> cap like `2` truncates the attack before it lands and **understates the bad-event
> rate**, and in an ACS A/B it hides violations the gate should be measured against.
> Keep `max_turns` **identical in the baseline and governed configs** (it changes
> elicitation depth, so a mismatch would break the "only ACS differs" comparison).
> If the risk is genuinely single-turn (a one-shot disclosure or a structural tool-arg
> failure), express that with `prompt` test cases rather than by shrinking the turn
> budget.

> **`stratify.dimensions` come from the approved research**, not straight from the
> parser. `research-eval-dimensions.md` uses the parser's `candidate_dimensions`
> (notably `elicitation_variant`, folded from the doc's **Variants**) as research
> *seeds*, then gates, expands, and deduplicates them. Entries may be explicit
> (`{name, description, levels[]}`) when the literature supports specific levels, or
> generated (`{name, description}`) otherwise — but a config must use **one mode
> throughout**, not a mix. When using generated mode, fold the parser's `values` list
> into the dimension's `description` (e.g. "Values: variant A; variant B; …") so the
> stratifier samples across the elicitation routes.

**Target shape:**
- Framework agent (LangGraph, CrewAI, …) with a Python entry function →
  `pipeline.inference.target.callable` **with** `target.trace`. Without traces the judge
  sees only 1 of 8 observability signals (final text); OTel traces expose all 8, including
  *intermediate* tool calls and routing — so tool-misuse and wrong-routing failures are
  effectively unscoreable without them. Use ASSERT's OTel auto-instrumentation
  (33 frameworks) rather than hand-writing spans; see `docs/targets/callable.md`.
  **The callable MUST accept a parameter named exactly `history`**
  (`def chat(message, history=None)`) — ASSERT detects multi-turn support by that
  parameter's *name*, so a callable that omits it (or calls it `messages` /
  `conversation`) silently receives only the latest turn, breaking multi-turn scenario
  cases (prior verification/context is dropped, inflating both the violation and
  `overrefusal` rates).
- Hosted model + system prompt (+ optional tools) → `target.model` / `target.tools`.
- Pre-collected traces → `assert-ai judge-traces --traces <path> --config <path>`.

## Step 4 — Atomicity (enforce)

**One atomic behavior per YAML.** Bundling makes `policy_violation`
a fuzzy logical-OR and masks per-behavior signal.

- A single Clarity failure mode is usually one behavior → one config.
- If a doc is flagged `multi_behavior` (e.g. failure-07 "operational **and**
  security risks" spanning cost overruns and prompt injection), **split** it into
  multiple candidates, name each specifically, and show the split in the triage
  list so the user chooses per split behavior.
- N selected behaviors → **N configs**, never one merged config.

## Step 5 — Confirm before running

For each generated config, show the user: `behavior.name`, `behavior.description`,
the stratify `dimensions`, the `target`, and the `judge` settings. Apply any
requested edits. **Run only on explicit go-ahead.**

## Step 5a — Smoke run before the full suite (offer by default)

A suite is 25 prompt + 25 scenario cases, and plumbing errors surface only once
inference starts — after systematize and test_set have already run. Offer a
smoke run on a few **real** cases first:

> Test set ready: 25 prompt / 25 scenario.
> Smoke test 3 prompt cases before the full run? [Y/n]

Skip the offer only when the user has asked to run everything unattended.

**1. Produce the artifacts without paying for inference.**

```
assert-ai run --config examples/<domain>/<risk>/eval_config.yaml \
  --override inference.enabled=false --override judge.enabled=false
```

Runs systematize and test_set only, producing the **full** taxonomy and the
**full** 25+25 test set, cached under the real config's key.

**2. Slice a few real rows.**

```
python .claude/skills/run-assert-eval/smoke_slice.py \
  --config examples/<domain>/<risk>/eval_config.yaml --count 3
```

Prints a JSON summary and writes `artifacts/smoke/<suite>-prompt-3.jsonl`. Take
the path from the summary's `out` field. Add `--kind scenario` only when the
risk is inherently multi-turn; scenario cases cost far more per case.

**3. Run inference and judge on the slice only.**

```
assert-ai run --config examples/<domain>/<risk>/eval_config.yaml \
  --override run=<run>-smoke \
  --override inference.test_set_path=<out path from step 2>
```

**4. Gate.** If the smoke run fails, **stop and report** — do not start the full
run. Typical causes: a wrong `target.callable` path, missing credentials, the
callable raising on its first tool call, a tool-schema mismatch, an undeployed
judge model. Fix, repeat step 3 (steps 1-2 remain valid), then continue.

The run prints its own `Headline:` block on success — target, judge model, and
the scored counts. To re-read it, or to show the user where it landed:

```
assert-ai results status <suite> <run>-smoke
```

and in the viewer it is a **run inside the existing suite**, not a new suite
card — the suite grid never shows it:

```
http://localhost:5174/suite/<suite>/<run>-smoke
```

A smoke run says the config *executes*. It says nothing about the rates — three
cases is not a measurement, so never report a number from it. Two things read as
breakage but are normal: the viewer's **audit/scenario tab is empty** (the slice
is prompt-only, so `viewer_audit_rows.json` is `[]`), and a dimension may show a
**smaller scored count than the slice size** when a case doesn't apply to it.

**5. Full run** — Step 6, unchanged. `systematize` and `test_set` report CACHED.

**Never substitute these:**

- **Do not** lower `pipeline.test_set.prompt.sample_size` for a cheap run. That
  block feeds the stage's `config_hash`, so changing it invalidates the cached
  test set and cascades into inference and judge. It also yields no subset:
  under `sampling.method: pairwise` the sample size is divided across the
  covering-array tuples, so a small value drops most tuples and case text is
  regenerated. You would validate cases the full run never scores, then pay to
  regenerate it.
- **Do not** reuse the real `run:` label. A separate label keeps smoke results
  out of the real run's directory and prevents writing its
  `.inference_config_hash` / `.judge_config_hash`.
- **Do not** write the slice inside the suite root — `smoke_slice.py` refuses,
  because it could clobber the published `test_set.jsonl`.

**Why the full run stays cheap:** `pipeline.test_set` is never modified, and
artifacts live at `<results_dir>/<suite>/artifacts/`, a sibling of the run dirs
keyed by suite rather than run — the same mechanism that lets `baseline` and
`acs-governed` share a cached systematization. Leaving systematize and test_set
enabled in step 3 costs nothing (both are cache hits) and keeps the judge
supplied with its taxonomy from context, so no `taxonomy_path` wiring is needed.

## Step 6 — Run sequentially

```
assert-ai run --config examples/<domain>/<risk>/eval_config.yaml
```

Run one at a time. Stream stage status (systematize → test_set → inference →
judge). After a smoke run the first two stages report CACHED; if either
regenerates, something changed the config — stop and find out what before
trusting the comparison. If one run fails, **report it and continue** with the
remaining configs. Note each `suite`/`run` for the report.

## Step 7 — Report

One results table, **one behavior per column, one experiment per row**, with:

- `policy_violation` and `overrefusal` rates reported **separately** (two
  different problems).
- Cited failure examples pulled from the run artifacts
  (`assert-ai results status <suite> <run>`, then `scores.jsonl` for
  `verdict.dimension_justifications`). Do **not** trawl raw traces.
- For each behavior, note the **source Clarity doc** and its intervention points
  ("a fix would target: …").

Offer next steps: raise `sample_size`, add a stratify dimension, apply an ACS guardrail at
the failing checkpoint, or **re-measure after a fix** to prove the rate dropped.

## Step 8 — Close the loop in Clarity

**Only when a `.clarity-protocol/` exists.** With user-supplied risks there is
nothing to write back to — skip this step, mention once that you're skipping it,
and instead offer Clarity discovery as a way to find failure modes this pass
didn't cover.

After a run, offer to write the outcome back into `.clarity-protocol/` via the
Clarity MCP tool **`record_suggestion`** (or **`record_decision`**): note that the
failure mode now has a **measured baseline** and where the eval lives
(`examples/<domain>/<risk>/eval_config.yaml`). This keeps Clarity's staleness tracking aware of the eval.

## Step 9 — Curate the example and handle discovery scratch

Do this at the end of the domain you just measured:

1. Keep one selected failure mode per config directory under `examples/<domain>/`.
2. Write or update the example README with the scenario, setup, run command,
   suite/run result path, and a concise behavior table.
3. Do not copy generated taxonomies, test sets, result artifacts, mailboxes,
   snapshots, or the raw protocol into `examples/`.
4. If the user needs the raw discovery record, export `.clarity-protocol/` to a
   user-owned location outside the example tree before the next discovery run.
   Otherwise state that the next run will overwrite it.

## Constraints (all mandatory)

- **One atomic behavior per config.** Never bundle.
- **Never start a fresh `run_clarity` over an unpreserved protocol.**
  `.clarity-protocol/` is gitignored scratch; overwriting it destroys the prior
  domain's discovery record with no git recovery. Run the preservation gate
  first (Entry conditions), export it outside `examples/`, or get an explicit
  discard instruction from the user.
- **Triage gate + pre-run confirmation are human decisions.** Never auto-run all
  discovered risks. Declining writes nothing and runs nothing.
- **`.clarity-protocol/` files are the source of truth.** Parser JSON is a
  disposable cache, never authoritative.
- **Do not modify clarity-agent source.** Consume its MCP server as shipped; if a
  capability is missing, note it as an upstream proposal.
- **Do not edit inside the Clarity-managed `AGENTS.md` block.**
- **Tolerant parsing.** Unknown severity labels or headers degrade to flagged
  candidates — never crash, never silently drop.
- **Customer-safe terminology.** Reference credential env var **NAMES** only
  (AZURE_API_KEY, AZURE_API_BASE, OPENAI_API_KEY, GITHUB_TOKEN, ANTHROPIC_API_KEY,
  azure_ad_token) — never values. Never read/print/commit `.env` or `artifacts/`.

## Worked example (one P1)

1. User: "measure the risks Clarity found for my support bot."
2. `failures.md` exists → parse. Top candidate is **`user_disengagement`** (P1),
   with an `elicitation_variant` dimension of 7 variants (challenging disposition,
   wrong calibration, happy-path attachment, cultural aversion, verbosity, unused
   protocol, alert fatigue). Treat those variants as **research seeds**, not the
   final dimension set.
3. Triage: user picks **P1s only** → just `user_disengagement`.
4. Collect both inputs that must never be silently defaulted: **`N`** (say they
   pick `3`) and **`sample_size`** (floor is `behavior_category_count`, so `25`;
   `50`+ = tightest — say they pick `25`).
5. Hand off to [`research-eval-dimensions.md`](research-eval-dimensions.md); do
   not hand-roll the config here:
   - **Intent intake** — the user says this gates a support-bot release, so
     `purposes: [product_readiness]`; decision and population left blank and not
     inferred.
   - **Reuse a spec** — `assert-ai library list` has no matching preset, so draft
     an inline description in the same `# Title` / `## Key Terms` /
     `## Behavior Categories` shape as the bundled specs. The slug settles as
     `user_disengagement`.
   - **Isolation preflight** — `plan_generation_path.py --eval-type harm --name
     user_disengagement --root examples/<domain>` finds no prior generation, so
     the run directory is `examples/<domain>/user_disengagement/`.
   - **Research** — `elicitation_variant` seeds the search; the retained axes are
     only those ≥2 independent sources support, with explicit `levels` where the
     literature supplies them. Judge dimensions are researched as their own
     namespace (e.g. `disengagement_severity`, `recovery_quality`) under new
     names — never `policy_violation` or `overrefusal`.
   - **`N` passes → dedup → approval** — 3 complete passes, semantic dedup within
     each namespace, then the three namespace tables go to the user. **Silence is
     not approval**; `validate_dimension_review.py pre-write` enforces it.
   - **Write** the cited config to
     `examples/<domain>/user_disengagement/eval_config.yaml`:
     `prompt.sample_size: 25` (applied to `scenario` too),
     `inference.max_turns: 6`, `judge.preset: safety-extended` plus the approved
     judge dimensions, inline `# sources:` comments, and a `# References` block.
6. Offer a smoke run (Step 5a): generate artifacts with
   `--override inference.enabled=false --override judge.enabled=false`, slice 3
   real prompt cases with `smoke_slice.py`, run them under `run=baseline-smoke`.
   They pass, so continue.
7. `assert-ai run` → systematize and test_set report CACHED → results table: one
   `user_disengagement` column.
   Headline the permissibility split from `results status --json` —
   `not_permissible_policy_violation_rate` (real harm got through) and
   `permissible_policy_violation_rate` (an allowed behavior was broken) — with
   `overrefusal` alongside as the separate availability check, plus 3–5 cited examples.
8. Offer `record_suggestion` back to Clarity: "user_disengagement now has a
   measured baseline at examples/<domain>/user_disengagement/eval_config.yaml."
9. Curate the example (Step 9): keep the atomic config and README, and export
   `.clarity-protocol/` outside `examples/` only if the user wants the raw record.
