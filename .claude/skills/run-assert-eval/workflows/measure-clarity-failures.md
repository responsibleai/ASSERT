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

For **each** selected behavior, produce its **own** flat config:
`evals/<atomic_behavior>.yaml`. Use a clear snake_case filename. Never bundle.

Config generation, in order of preference:

1. **Built-in preset first.** `assert-ai library list` shows the bundled behavior
   and judge presets (e.g. `prompt_injection`, `doxxing`, `stereotyping`,
   `sycophancy`, `harmful_medical_advice`, `tool_orchestration_errors`);
   `assert-ai library show <name>` prints one. If a preset matches the risk, seed
   from it: `assert-ai init --behavior <name>` and/or `--judge-preset <name>`.
2. **Domain template next.** Check the ASSERT `examples/` directory for a vetted
   config matching the risk type; copy it as the base and adapt.
3. **Otherwise** generate from the schema:
   `assert-ai init --default-model <litellm-model> --describe-file <text-path> --non-interactive -o <path>`.
   Write the failure-mode text (failure mode + how it arises + target context) to
   a file first. It is Clarity-derived prose you did not author, so a quote,
   backtick, or `$(...)` in it would break or inject into the shell if
   interpolated into `--describe "<text>"`.

Fill from the candidate behavior (real schema field names):

| Config field | Source |
| --- | --- |
| `behavior.name` | candidate `name` (short, specific) |
| `behavior.description` | candidate `description` (the doc **Summary**, tightened to a *testable* statement) |
| `context` | Clarity `summary.md` / `goal/requirements.md` / `solution/architecture.md` |
| `default_model.name` | the cheap model — drives the target, test-set generation, and tester (e.g. `azure/gpt-5.4-mini`) |
| `pipeline.systematize.model` + `pipeline.judge.model` | **pin both to the strong model** (e.g. `azure/gpt-5.4`). `init` has no flag for these, so they inherit `default_model` unless you edit the config by hand — see the ground-truth note below |
| `pipeline.test_set.stratify.dimensions` | `candidate_dimensions` — **include the `elicitation_variant` dimension** derived from the doc's Variants |
| `pipeline.test_set.prompt.sample_size` | **ask the user (see the sizing note below)** — do not pick silently; recommend `25` (or `≥25` for an ACS A/B), offer `10` for a throwaway first look |
| `pipeline.test_set.scenario.sample_size` | same — ask once and apply the user's answer to **both** `prompt` and `scenario` unless they say otherwise (`≥25` when the run will feed an ACS before/after A/B — see `govern-and-remeasure.md`) |
| `pipeline.inference.target` | the target shape (see below) |
| `pipeline.inference.max_turns` | **set to `10`** (the ASSERT default). Do **not** leave it low (e.g. `2`) — see the multi-turn note below. Use the **same** value in the baseline and governed configs. |
| `pipeline.judge.preset` | leave `dimensions` **unset** — `policy_violation` and `overrefusal` are built in and always judged (see the built-in note below) |

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

> **Do not author judge `dimensions`.** `policy_violation` and `overrefusal` are
> `BUILT_IN_DIMENSIONS` (`assert_ai/core/judge.py`) and are **always judged**
> unless explicitly disabled — you get both for free with no `dimensions` block.
> Config dimensions are merged over the built-ins **by name**, so declaring one
> called `policy_violation` or `overrefusal` silently **replaces the built-in
> rubric** with a hand-written one. Only add a dimension for a genuinely new
> metric the built-ins don't cover, and never reuse a built-in name.

> **Built-in `policy_violation` couples with `overrefusal` — read the split instead.**
> The built-in `policy_violation` dimension is the logical-OR over ALL violated
> taxonomy nodes — including *permissible* ones — so over-gating a permissible
> behavior also trips it, and it can never be fully separate from `overrefusal`.
> For a plain baseline that's usually fine. When you need the decoupled numbers
> (any ACS before/after A/B — see `govern-and-remeasure.md`), don't restructure the
> config: `assert-ai results status <suite> <run> --json` already reports the
> headline pair — `not_permissible_policy_violation_rate` (real harm) and
> `permissible_policy_violation_rate` (allowed behavior broken) — each one vote per
> conversation. The split is derived from stored judgments, so it needs no config
> change and works on existing runs. In the viewer the same pair appears as the
> dimension keys `policy_violation_not_permissible` / `policy_violation_permissible`,
> labelled **Impermissible behavior violated** / **Permissible behavior violated**. When the
> split is present the viewer now **hides** `policy_violation` / `overrefusal` as
> superseded — they are still judged, aggregated, and written to artifacts.

> **Sizing for noise (why the first-run "10" is often too small).** Each rate is
> `violations / sample_size`, so at `sample_size: 10` **one flipped case moves the
> number 10 percentage points**. Inference is non-deterministic (agent temperature
> is 1.0; gpt-5 models can't be pinned lower), so two independent runs of the *same*
> config drift by a case or two purely by chance. That noise is harmless for a quick
> "is it broken?" look, but it **wrecks an ACS before/after A/B**: a phantom ±10pp
> swing on a small sample can masquerade as a governance effect (or hide one).
>
> **Always ask the user for the sample size before generating the config — do not
> pick it silently.** Present the tradeoff in one line and let them choose, e.g.:
> *"How many cases per behavior should I sample? `10` = fast/noisy first look,
> `25` = stable rate (recommended), `50`+ = tightest signal. Cost scales linearly.
> I'll use the same size for prompt and scenario."* Recommend `25` as the default,
> and **`≥25` whenever the run will become an ACS A/B baseline** (the governed
> config is a byte-identical copy that inherits this size — see
> `govern-and-remeasure.md`). If the user has no preference, default to `25` (or
> their first-look `10` only if they explicitly want a throwaway pass).

> **Set `pipeline.inference.max_turns: 10`; do not leave it low (e.g. `2`).**
> `max_turns` caps the alternating tester↔target loop for **scenario** (multi-turn)
> cases (single-turn `prompt` cases ignore it). `10` is the ASSERT default
> (`DEFAULT_TESTER_MAX_TURNS`) and gives a realistic persistence/erosion arc room to
> land — many of the strongest findings are **multi-turn erosion** (the agent holds
> firm for a few turns, then softens into a dose/clearance/leak under pressure). A low
> cap like `2` truncates the attack before it lands and **understates the bad-event
> rate**, and in an ACS A/B it hides violations the gate should be measured against.
> Keep `max_turns` **identical in the baseline and governed configs** (it changes
> elicitation depth, so a mismatch would break the "only ACS differs" comparison).
> Only lower it (`4`–`6`) if the risk is genuinely single-turn (a one-shot disclosure
> or a structural tool-arg failure) *and* the user wants a cheaper run.

> `stratify.dimensions` entries are `{name, description}`. Fold the parser's
> `values` list into each dimension's `description` (e.g. "Values: variant A;
> variant B; …") so the stratifier samples across the elicitation routes.

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

## Step 6 — Run sequentially

```
assert-ai run --config evals/<atomic_behavior>.yaml
```

Run one at a time. Stream stage status (systematize → test_set → inference →
judge). If one run fails, **report it and continue** with the remaining configs.
Note each `suite`/`run` for the report.

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
(`evals/<atomic_behavior>.yaml`). This keeps Clarity's staleness tracking aware of the eval.

## Step 9 — Curate the example and handle discovery scratch

Do this at the end of the domain you just measured:

1. Keep one selected failure mode per YAML under `evals/`.
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
   protocol, alert fatigue).
3. Triage: user picks **P1s only** → just `user_disengagement`.
4. **Ask the user for `sample_size`** (recommend `25`; `10` = quick look, `50`+ = tightest). Say they pick `25`.
5. Generate `evals/user_disengagement.yaml`: `behavior.description`
   from the doc Summary, `stratify.dimensions` includes `elicitation_variant`
   (7 values folded into its description), `prompt.sample_size: 25` (the size the
   user chose, applied to `scenario` too), `inference.max_turns: 10`, and **no
   `judge.dimensions` block** — `policy_violation` + `overrefusal` are built in.
6. Confirm → `assert-ai run` → results table: one `user_disengagement` column.
   Headline the permissibility split from `results status --json` —
   `not_permissible_policy_violation_rate` (real harm got through) and
   `permissible_policy_violation_rate` (an allowed behavior was broken) — with
   `overrefusal` alongside as the separate availability check, plus 3–5 cited examples.
7. Offer `record_suggestion` back to Clarity: "user_disengagement now has a
   measured baseline at evals/user_disengagement.yaml."
8. Curate the example (Step 9): keep the atomic config and README, and export
   `.clarity-protocol/` outside `examples/` only if the user wants the raw record.
