---
name: run-assert-eval
description: >
  Run an ASSERT evaluation starting from Clarity-discovered risks. Use when the
  user wants to evaluate, test, or check an AI agent, LLM app, or model against
  requirements/policies (e.g. "evaluate my agent for budget violations", "test
  that the support bot never gives legal advice"). Drives the real Clarity MCP
  tools (run_clarity) in-IDE to discover risks, then generates one flat
  evals/<atomic_behavior>.yaml per selected risk, runs the pipeline, and reports
  pass/violation rates with trace-cited failure examples.
---

# Run an ASSERT evaluation

## When to use

The user wants evidence of how their agent or model actually behaves. Not for
fixing the agent — this skill finds and reports failures.

This skill has two entry modes:

- **Run mode** — no usable results exist yet. Risks come from **Clarity** (Steps 1-2):
  either an existing `.clarity-protocol/` directory or a fresh discovery run driven
  through the **Clarity MCP server** (`run_clarity`), in-IDE. Then turn each selected
  risk into an atomic config, run the pipeline (Steps 3-5), and report (Step 6).
- **Results Q&A mode** — judged artifacts already exist under
  `artifacts/results/<suite>/<run>/` and the user asks a *question* about them
  ("what are the highlights?", "top 3 examples of the worst failure mode?", "why
  did case X fail?"). Skip to Step 6 and answer THAT question from the artifacts —
  do not re-run, and do not fall back to the full canned report unless asked.

### Clarity is required for Run mode — no non-Clarity fallback

Risks that seed an eval MUST come from Clarity (an existing `.clarity-protocol/`
or a fresh discovery run via the Clarity MCP `run_clarity` tool). Do **not**
substitute a plain-language description, and do **not** imitate Clarity's
questioning from your own head — instead, `run_clarity` returns Clarity's real
process guide inlined, and you follow *that* to conduct the clarifying loop. An
eval spec that skips Clarity's captured risks produces inaccurate, low-signal
results. If the Clarity MCP tools are not available, STOP and help the user set
them up (see `SETUP-CHECKLIST.md`) rather than proceeding.

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

2. **Clarity MCP server available** (required for Run mode): the `clarity-agent`
   MCP tools (`run_clarity`, `write_protocol_document`, `record_failure`,
   `record_suggestion`, …) are callable in this session. Clarity is the
   risk-discovery engine — the skill drives its real MCP tools, it does not
   reimplement it. If the tools are missing, the server is not wired up yet: guide
   the user through `SETUP-CHECKLIST.md` (install `clarity-agent` with the `[mcp]`
   extra, run `clarity embed .` to generate `.vscode/mcp.json`, reload MCP servers)
   and confirm the LLM provider is configured (`clarity doctor` — Clarity supports
   GitHub Copilot, Anthropic, OpenAI, Azure AI, and Gemini).

   If the Clarity MCP tools cannot be made available, STOP and help the user
   resolve it. Do not proceed with a non-Clarity path.

3. **Provider creds exist** in `.env`. NEVER read or print `.env`. If a run fails
   with an auth error, tell the user which variable NAMES are required
   (AZURE_API_KEY, AZURE_API_BASE, OPENAI_API_KEY, GITHUB_TOKEN, ANTHROPIC_API_KEY,
   etc.) — never their values.

## Steps

### 1. Discover risks with Clarity (required front door)

Risks come from Clarity's real engine, driven through the **Clarity MCP server** —
never from a plain-language guess and never by imitating Clarity from your own head.

- **If a `.clarity-protocol/` directory already exists** in the workspace, use it
  directly as the risk source — skip straight to reading its output below.
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

### 2. Triage — choose which risks to measure now

Clarity intentionally over-produces (whole-lifecycle threat modeling). Do NOT
auto-generate an eval for every failure mode. Surface the enumerated list (ordered
by Clarity's severity signal) and ask the user which to measure now (e.g.
"top-severity only?", or named picks). Carry only the selected risks forward.

### 3. Turn each selected risk into an atomic config

ASSERT performs best with **one atomic behavior per eval**. Never bundle multiple
risks into one config — bundling makes `policy_violation` a fuzzy logical-OR and
hides per-behavior signal.

- **1 selected risk** → generate one config and run once.
- **N selected risks** → generate N flat `evals/<atomic_behavior>.yaml` files
  and run them sequentially, one per behavior.

For each selected risk, map the Clarity failure mode → `behavior.name` +
`behavior.description`, and use its context for `context`:

```
assert-ai init --default-model <litellm-model> --describe-file <path> --non-interactive -o evals/<atomic_behavior>.yaml
```

- **Write the description to a file and pass `--describe-file`.** The text is
  Clarity-derived prose you did not author, so it can contain quotes, backticks,
  or `$(...)`. Interpolating it into `--describe "<text>"` would break the
  command or inject into the user's shell. `--describe` stays available for
  short text you typed yourself; the two are mutually exclusive.
- `--default-model` seeds the generated config's `pipeline.default_model` — the
  model the **eval** runs against. Do **not** use `--model` for this: that is the
  model driving the init assistant's own conversation (default
  `azure/gpt-5.4-mini`) and it has no effect on the eval. Note `--default-model`
  is a prompt-level hint the design agent is asked to *confirm*, not a
  deterministic write — verify the value actually landed in the generated YAML.
- **Pin `systematize` and `judge` to the strong model by hand after init.**
  `init` has no `--systematize-model` / `--judge-model` flag, so everything
  inherits `default_model` unless you edit the config. Run the eval on the
  cheap model and the two stages that define and apply ground truth on the
  strong one:

  ```yaml
  default_model:
    name: azure/gpt-5.4-mini      # target, test-set generation, tester
  pipeline:
    systematize:
      model: azure/gpt-5.4        # authors the taxonomy
    judge:
      model: azure/gpt-5.4        # renders every verdict
  ```

  This is the convention in the repo's own examples (`benchmark`,
  `change_control_agent`, `incident_triage_agent`, `phoenix_auto_trace`,
  `science_research_agent`). These two stages are not ordinary stages:
  `systematize` authors the behavior tree and the permissible /
  non-permissible split that **every** metric is computed against, and
  `judge` decides both applicability and violation for every row — with no
  redundancy, since `judge.n` defaults to `1` and judge temperature is not
  pinned. A weak model here does not add noise around a fixed target, it
  moves the target, and it inflates run-to-run drift in applicability and
  in small deltas. Skipping this silently downgrades both.
- **Check the built-in presets first** — `assert-ai library list` shows bundled
  behavior and judge presets (`prompt_injection`, `doxxing`, `stereotyping`,
  `sycophancy`, `harmful_medical_advice`, `tool_orchestration_errors`, …);
  `assert-ai library show <name>` prints one. If one matches the risk, seed with
  `--behavior <name>` / `--judge-preset <name>` instead of generating from scratch.
- **If the user has an existing config** to extend, use `--from <path>` instead of
  generating from scratch.
- After generation, show the user the generated `behavior.description`, `context`,
  and `pipeline.judge` settings, plus the resolved `systematize` / `judge`
  models. Confirm before running.
- **Do not author judge `dimensions`.** `policy_violation` and `overrefusal` are
  `BUILT_IN_DIMENSIONS` (`assert_ai/core/judge.py`) and are always judged unless
  explicitly disabled, so no `dimensions` block is needed. Config dimensions are
  merged over the built-ins **by name**, so declaring one with a built-in name
  silently replaces that built-in's rubric. Add one only for a genuinely new
  metric the built-ins don't cover, and never reuse a built-in name.

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

```
assert-ai run --config evals/<atomic_behavior>.yaml --output json
```

This is long-running (systematize -> test_set -> inference -> judge). Stream status
to the user as each stage completes. For N configs, run them sequentially and track
each `suite`/`run`.

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

2. **Top failing cases**: read `scores.jsonl` from `artifacts/results/<suite>/<run>/`.
   For each dimension with failures, pull 3-5 representative cases with:
   - The test case description (what was tested)
   - `verdict.dimensions` — which dimensions failed
   - `verdict.dimension_justifications` — the judge's rationale with cited evidence
   - `verdict.node_judgments` — which behavior categories were violated, with reasoning

3. **Cost and timing**: read `metrics.json` for token usage and elapsed time per stage.
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

## Guardrails

- **Clarity is the required risk source** — for Run mode, risks come from Clarity (existing `.clarity-protocol/` or a fresh discovery run via the `run_clarity` MCP tool). Never substitute a plain-language guess or imitate Clarity's questioning from your own head; if the MCP tools can't be made available, stop and help fix it (`SETUP-CHECKLIST.md`).
- **Drive the real Clarity MCP tools in-IDE** — use `run_clarity` / `write_protocol_document` / `record_failure` for discovery and `record_suggestion` to close the loop; never hand the user off to a separate Clarity app and never shell out to a `clarity cli` process.
- **Close the loop** — after a run, offer `record_suggestion` (or `record_decision`) back into `.clarity-protocol/` noting the failure mode now has a measured baseline and where the eval lives, so Clarity's staleness tracking stays aware of it.
- **Govern with ACS, don't just prompt-tweak** — to fix and *prove* it, generate an ACS policy from the findings (`assert-ai acs generate`), review it (scope the gated tools, tighten conditions), and re-run the same eval against the governed callable to show the delta; needs a wrappable callable target (`workflows/govern-and-remeasure.md`). Generated policies, guarded targets, and governed configs are local run output by default. Commit them only in the user's own product repo when the user wants a reviewed policy deployed; do not automatically add them to ASSERT's worked examples. Whenever a gate needs a value the model doesn't put in the tool args — a trusted session flag (verification), a trusted comparison value (the caller's own id), a trusted numeric cap, or a running total / prior-call fact — the governed agent must surface that scalar from its **session state** into the tool-call **policy_target** so the generated `input.policy_target.value.*` rule actually fires. ACS evaluates each call in isolation, so multi-call constraints (running totals, ordering, rate limits) are handled by that same injection, not by encoding history in Rego. Free-form content failures (unsafe advice, PII in prose, a verbal-only high-risk promise) and inbound prompt-injection instead use an **annotator-based** gate at the `output`/`input` point, proven by the remeasure delta since offline `validate` can't run annotators. Never hand-drive an external `acs` CLI for this loop.
- **Organize by domain across runs** — prefix every eval **suite name** with a domain slug (`<domain>-<risk>`, e.g. `billing-cross-customer-data-exposure`, `science-<risk>`), so `artifacts/results/<suite>/` and `artifacts/acs/<suite>/` do not collide. Treat `.clarity-protocol/` as uncommitted single-domain scratch; preserve it outside `examples/` only when the user asks.
- **Per-example package** — every worked example must be a small, self-contained folder under `examples/<domain>/` containing only what a customer needs to understand and reproduce the ASSERT run:
  - `agent.py` (+ any real runtime deps it imports, e.g. `tools.py` / `mock_tools.py`) — the runnable baseline.
  - `README.md` — scenario, setup, atomic behaviors, run commands, and result paths.
  - `evals/<atomic_behavior>.yaml` — one independently runnable baseline config per behavior.
  A deliberately curated ACS demonstration may additionally keep the smallest
  reviewed policy, guarded target, and governed config needed to reproduce its
  claim, but ordinary worked examples must not accumulate generated governance
  output. Do not commit generated taxonomies, test sets, result artifacts,
  discovery mailboxes, snapshots, protocol archives, or automatic skill output.
- **One atomic behavior per config** — split N selected risks into N configs run sequentially; never bundle.
- **Triage before running** — never auto-generate an eval for every Clarity failure mode; ask which to measure now.
- **Don't invent metrics** — only report what's in the artifacts.
- **Don't trawl raw traces to answer questions** — answer from `results status`, `scores.jsonl`, and `metrics.json`; hand off to the viewer for visual trace/transcript exploration.
- **Hand off, don't reimplement the viewer** — for visual drill-down, baseline compare, or live monitoring, point to the local viewer rather than reproducing it in chat.
- **Don't read, print, or commit** `.env`, credential values, `artifacts/`, traces, `.venv`, or logs.
- **Reference env variable NAMES only** (AZURE_API_KEY, AZURE_API_BASE, azure_ad_token, GITHUB_TOKEN, ANTHROPIC_API_KEY) — never values.
- **Don't commit artifacts** to the repository.
