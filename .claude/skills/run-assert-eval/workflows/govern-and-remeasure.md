# Workflow: govern-and-remeasure

Turn a measured ASSERT failure into a deployable **ACS** (Agent Control
Specification) policy, then re-run the same eval against the governed agent to
**prove the failure rate dropped** — the ACS delta.

This is the governance half of the story and picks up where
`measure-clarity-failures.md` leaves off: Clarity discovered the risk,
ASSERT measured a baseline violation rate, and now ACS governs the failure at
runtime. It uses ASSERT's **native** ASSERT to ACS adapter (`assert-ai acs …`),
which derives the policy straight from the run's findings — no external `acs`
CLI and no separate checkout of the agent-governance-toolkit are needed.

> **Everything stays in-IDE.** ACS has no MCP server; the `assert-ai acs`
> subcommands are the in-IDE surface, driven the same way ASSERT already drives
> the rest of the pipeline. Do not hand the user off to a separate app.

## Placeholders

Throughout this workflow, substitute your own domain's names for the
placeholders: `<eval-dir>` (the directory holding the eval config), `<suite>`
(the eval `suite:`), `<baseline-callable>` / `<governed-callable>` (the two
`module:function` entrypoints), and `<bad-event>` (a short label for the harmful
event you are gating, used as the Rego deny `reason`).
`examples/billing_support_agent/agent.py` shows the shape of a baseline callable
target. The governed counterpart, the policy, and the eval configs are **outputs
of this workflow**, not checked-in files — nothing here is specific to billing.

## Preconditions (check, don't assume)

1. **A measured baseline run exists** for a callable target, reporting a genuine
   violation signal — violated non-permissible taxonomy nodes, which is exactly
   what the headline split counts (see Step 1). The adapter reads `scores.jsonl`,
   `inference_set.jsonl`, and `taxonomy.json` from
   `artifacts/results/<suite>/<run>/`, keying its guardrail off the violated
   non-permissible nodes in `node_judgments` (not the `policy_violation`
   dimension) — the same source the permissibility split is derived from.
   **Sized for a stable delta:** because this baseline's test set is *reused* by
   the governed run (byte-identical config), the whole A/B inherits its
   `sample_size`. At `sample_size: 10` one flipped case is ±10pp of noise that can
   masquerade as — or bury — the governance effect. If the baseline was a quick
   first pass at `10`, **ask the user to confirm a larger size (recommend `≥25`),
   then raise `sample_size` in the baseline config and re-run it before comparing**
   (see the sizing note in `measure-clarity-failures.md`).
2. **The ACS extra is installed**: `python -m pip install -e ".[acs]"` (pulls in
   the `agent-control-specification` SDK). Verify with `assert-ai acs --help`.
3. **`opa` is on PATH** (Open Policy Agent) — required to evaluate the generated
   Rego. Without it every verdict fails closed to `deny`.
4. **Provider creds exist** in `.env` for policy generation (`assert-ai acs
   generate` uses an LLM by default). NEVER read or print `.env`; reference
   variable NAMES only (AZURE_API_KEY, AZURE_API_BASE, …).

## Step 0 — Confirm a wrappable target

ACS enforces at real tool-call boundaries (`pre_tool_call` / `post_tool_call`);
the `guard_target` input/output path alone does **not** enforce tool gates. A
failure that lives at a tool call can therefore only be governed by a real
callable whose tool functions are wrapped with `control.protect_tool` — a
hosted-model Prompt Agent (simulated tools, gate in the system prompt) has
nothing wrappable and cannot demonstrate the delta.

If the eval currently targets a hosted model, switch to a callable target first:
implement the agent as a Python tool loop with real tool functions (mirror the
declared toolset), wire OTel spans for `target.trace` (ASSERT's auto-instrumentation
covers 33 frameworks — see `docs/targets/callable.md`; hand-written spans are rarely
needed), and expose two
entrypoints — an ungoverned baseline and an ACS-governed variant that wraps its
high-risk tools with `control.protect_tool`. See
`examples/billing_support_agent/agent.py` for the shape of the baseline half; you
create the governed half in Step 3.

## Step 1 — Baseline run (Run A)

Run the ungoverned callable target to establish the **ASSERT Baseline %**:

```
assert-ai run --config evals/<atomic_behavior>.yaml
```

Note the `suite` and `run` (e.g. `baseline`). Report the headline pair and
`overrefusal` separately per `measure-clarity-failures.md` Step 7.

> **The headline pair is the permissibility split.** The built-in
> `policy_violation` dimension is the OR of ALL violated taxonomy nodes —
> including *permissible* ones — so over-gating a permissible behavior also trips
> it, making ACS *look* like it raised the failure rate when it only added a block.
> Never headline that number in an A/B.
>
> Report **both halves of the split** instead. Each is one vote per conversation,
> derived (not judged) from `verdict.node_judgments` plus the run's `permissible`
> taxonomy flag — `compute_policy_violation_by_permissibility` in
> `assert_ai/results.py`:
>
> | Half | Means | Under ACS |
> |---|---|---|
> | **non-permissible** violation | real harm got through | should **drop** |
> | **permissible** violation | the agent broke a behavior it was allowed to do | should stay **flat** |
>
> A drop in the first with the second flat is the win condition. A drop in the
> first bought by a rise in the second is over-gating, not governance.
>
> The two halves are named differently on each surface — use the right one:
>
> | Surface | Real harm | Allowed behavior broken |
> |---|---|---|
> | `results status --json` | `not_permissible_policy_violation_rate` | `permissible_policy_violation_rate` |
> | viewer dimension key | `policy_violation_not_permissible` | `policy_violation_permissible` |
> | viewer on-screen label | **Impermissible behavior violated** | **Permissible behavior violated** |
>
> The viewer renders every metric through `metricTitleLabel`
> (`viewer/src/lib/labels.ts`), so the raw keys never appear in the UI — when
> reporting from a screenshot or an exported HTML, quote the on-screen label and
> map it back to the `--json` key yourself. Note the display label says
> "Impermissible" while every identifier says `not_permissible`; don't
> cross-contaminate them.
>
> `permissible` is a **required** taxonomy field (`stages/systematize.py`), and the
> split is recomputed from stored judgments — so it is always available, including
> for runs judged before the split existed, with no config changes and no
> re-judging. Keep `overrefusal` alongside as the separate availability metric.

## Step 1a — Classify the failure BEFORE you generate (the one-pass step)

Most wrong deltas are not tuning failures — they are a gate built at the **wrong
interception point**, and that is decidable from the baseline run you already
have. Answer these five questions before `acs generate`. It costs one pass over
`scores.jsonl` and removes most of `diagnose-acs-delta.md`.

Work only over the **flagged non-permissible rows** — the ones that constitute
`not_permissible_policy_violation_rate`.

**1. Where does the judge say the harm happened?** Read
`verdict.dimension_justifications` on those rows.

| Justifications cite | Failure is | Gate at |
| --- | --- | --- |
| the **reply text** ("presents unsupported values as fact", "claimed approved", "in the draft") | **semantic** | `output` annotator |
| **tool args or results** (a value passed, a record written, a field returned) | **structural** | `pre_tool_call` / `post_tool_call` |

**2. Does the harm actually route through a tool?** Count flagged rows whose
`llm_calls` include the tool you intend to gate.

- **Only a minority of flagged rows call it** → a tool gate structurally cannot
  reach the rest. Gate at `output`; keep any tool rule as defense-in-depth only.
- **Nearly all flagged rows call it** → a structural gate is viable.

> A deterministic field in `tools.py` (`fabricated_fields`, `sequence_violations`,
> a `verified` echo) does **not** make the failure structural — what matters is
> what the **judge** scores. Observed: change_control_agent exposed
> `fabricated_fields`, but only 3/50 flagged rows called the tool, so the
> structural gate blocked 0/50 and the rate held at ~56%.

**3. Is the entitlement signal trustworthy?** Only if the gate depends on "who is
allowed":

- **Trusted session state** (a `verified` flag the host sets) → surface it into
  the policy_target; a structural rule is fine.
- **Spoofable / model-inferred** (a `verify_identity` tool keyword-matching the
  caller's self-description, a self-asserted role) → do **not** condition on it.
  Calibrate the annotator to the **judge's** standard using the user's turns.

**4. Is it multi-turn?** If the config has scenario cases (`max_turns > 1`), then
**both** are mandatory up front:

- the callable declares `history` and the wrapper gates **every** turn — the judge
  scores the whole transcript, so one missed early turn keeps the case flagged no
  matter what a later block does; and
- the annotator **and** the regenerate step both receive `history`, or
  prior-turn/user-supplied facts look "unsupported" on a follow-up turn and you
  over-block legitimate answers.

**5. Go / no-go — stop before building if:**

- **baseline non-permissible rate ≲10%** → not a governance target. Run the
  governed pass once to confirm no-harm, record it, move on.
- **two selected risks share one content band** (e.g. harmful dosing vs. general
  medication education) → the judge will score the same sentence as harm under one
  rubric and as overrefusal-if-withheld under the other. Define the boundary once,
  accept a modest permissible/overrefusal rise, and don't iterate against it.
- **the target is a YAML Prompt Agent** → materialize a faithful callable first
  (Step 0); there is no seam to wrap.

**Record the four answers** — gate point, tool coverage, entitlement source,
history required — before running `acs generate`. If you cannot answer #1 and #2
from the baseline artifacts, you are guessing, and the delta will tell you so.

## Step 2 — Generate the ACS policy from the findings

```
assert-ai acs generate --suite <suite> --run baseline \
  --out artifacts/acs/<suite>
```

Writes `manifest.yaml`, `policy/<slug>.rego`, and `report.md`. The generator
builds the guardrail from **structured findings signal only** (violated taxonomy
node, its permissibility, per-node rate, violated intervention points, violating
tool names) — raw transcript text is deliberately not sent to the model. For a
tool-gate failure the rules land at `pre_tool_call` / `post_tool_call`.

- Thresholds: `--min-rate` / `--min-count` to include only material findings.
- `--no-validate` to skip the built-in validation pass.
- `--model azure/<deployment>` (e.g. `azure/gpt-5.4`) so litellm uses the Azure
  path. `assert-ai acs` loads the project `.env` automatically (same as
  `assert-ai run`) — do NOT hand-export credentials into the shell.

**Review the generated Rego and `report.md`, then COMMIT the reviewed policy** and
enforce that committed copy (don't regenerate on every run, and don't enforce
straight from gitignored `artifacts/`). `acs generate` output is a **draft**: an
LLM authored it from the findings, so review it against this checklist before
committing:

- **Tool coverage.** The generator only gates tools it *observed* violating in the
  sample — it commonly **omits** in-class tools that didn't happen to be called
  and **includes** over-broad ones (read-only lookups, `escalate`). Add the
  missing tools of the same class; drop the ones that shouldn't gate (guarding
  unrelated tools inflates `overrefusal`). Declare every gated tool in `tools:`.
- **The condition reads a field that exists** (see Step 2a — this is where a
  structural gate silently no-fires or over-denies).
- **Both `pre_tool_call` and `post_tool_call` are declared** for a guarded tool,
  or the runtime fails closed to `deny`.
- **Harden loose comparisons** — `input.policy_target.value.verified == false`
  silently passes when the field is absent; prefer `not input.policy_target.value.verified`.

Keep the reviewed manifest + Rego in **version control** (not under `artifacts/`)
and point the governed agent at it. Convention: commit the policy beside the
example it governs as `<example-dir>/acs/<slug>/`, and have the governed agent
default its manifest path there.

## Step 2a — Make the generated condition read a field that exists

`acs generate` conditions **structural** rules on `input.policy_target.value.*`
(the tool args at `pre_tool_call`, the result at `post_tool_call`),
`input.tool.name`, and constants. It is **not** permitted to read
`input.snapshot.*`. It also emits **annotator-based** rules over
`input.annotations.<classifier>.*` for semantic content. Which style you get — and
whether it enforces — depends on what the failure conditions on:

- **Semantic / content failures** (toxicity, PII leakage, jailbreak phrasing,
  unsafe advice) — **keep the annotator-based policy.** There's no structural
  field to key on; an LLM judgment is right, and the ACS host populates
  `input.annotations.*` at runtime. (It stays empty under offline `validate`, so
  the gate *looks* inert there — that's expected, not a defect. Verify it via the
  guarded remeasure run, not `validate`.)
- **Structural / session-state gates** (cross-tenant account scoping, refund-cap
  arithmetic, a required verification flag) — the generated deterministic rule is
  the right shape, but it only enforces if the field it reads is actually present
  in `input.policy_target.value`.

**The gotcha that makes "ACS do nothing" or "make it worse":** a session-state
gate (e.g. "must be verified") depends on state the model does NOT put in the tool
args. The generator, restricted to `input.policy_target.value.*`, emits something
like `input.policy_target.value.verified == false` — but the tool args have no
`verified` field, so the rule either never fires (bypass persists) or, with a
`not`, denies unconditionally (blocks verified users → `overrefusal` spikes).

**The fix is agent-side, and it keeps the generated Rego authoritative:** have the
governed agent **surface the trusted session field into the tool-call
policy_target**, sourced from its own session state (never from the model's
arguments), so the generated `input.policy_target.value.<field>` comparison reads
a real value. Strip the injected keys before the real tool runs. The billing
worked example does exactly this: `_policy_target_args` / `_POLICY_CONTEXT_KEYS`
inject the trusted `verified` flag into the
policy_target, so the generated `input.policy_target.value.verified` rule enforces
the identity gate. For an **argument** gate (e.g. tenant scoping) the discriminating
value is already a real tool arg, so no injection is needed — but you still want a
trusted comparison value (inject the caller's own account id rather than trusting a
second arg).

**The real OPA input contract** (what Rego actually sees — do not guess
`input.tool_call.*`, that path is wrong):

| Path | Value |
| --- | --- |
| `input.tool.name` | the tool name being called |
| `input.policy_target.value` | the resolved policy target — at `pre_tool_call` with `policy_target: $.tool_call.args` this is the **args dict** (`input.policy_target.value.<arg>`), including any trusted context the agent injects; at `post_tool_call` with `policy_target: $.tool_result` it is the **result** |
| `input.annotations.<classifier>.*` | LLM-annotator outputs — populated at runtime, empty under offline `validate` |
| `input.snapshot.*` | the agent's per-call snapshot — available to a hand-written policy, but NOT emitted by `acs generate` |

Reviewed deterministic shapes (what a committed policy looks like after the
review + agent-side injection above):

```rego
package agent_control_specification.<slug>

import rego.v1

default pre_tool_call_verdict := {"decision": "allow"}

guarded_tools := {"<tool_a>", "<tool_b>"}   # the in-class tools for your failure

# Shape 1 — SESSION-STATE gate. The agent injects the trusted `verified` flag into
# the policy_target, so this reads a real value (`not` fires on false OR missing).
pre_tool_call_verdict := {"decision": "deny", "reason": "<bad-event>"} if {
    input.intervention_point == "pre_tool_call"
    input.tool.name in guarded_tools
    not input.policy_target.value.verified
}

# Shape 2 — ARGUMENT gate. Compares a tool ARG against a TRUSTED value the agent
# injects (the caller's own id), not a second user-supplied arg.
pre_tool_call_verdict := {"decision": "deny", "reason": "<bad-event>"} if {
    input.intervention_point == "pre_tool_call"
    input.tool.name in guarded_tools
    requested := input.policy_target.value.account_id
    requested != ""
    requested != input.policy_target.value.caller_account_id   # injected, trusted
}

# Shape 3 — NUMERIC / THRESHOLD gate. Deny when a numeric arg exceeds a TRUSTED
# cap the agent injects (never a user-supplied limit). The `is_number` guard is
# required: a bare `>` errors or misfires when the field is a string or absent, so
# an unguarded rule silently no-fires (bypass persists). Compare against the
# injected cap, not a constant, so one policy serves callers with different caps.
pre_tool_call_verdict := {"decision": "deny", "reason": "<bad-event>"} if {
    input.intervention_point == "pre_tool_call"
    input.tool.name in guarded_tools
    amount := input.policy_target.value.amount
    is_number(amount)
    amount > input.policy_target.value.max_amount   # injected, trusted cap
}
```

Pair each `pre_tool_call` rule with a matching `post_tool_call` rule (defense in
depth on the result), and declare **both** intervention points in the manifest —
a guarded tool that declares only one fails closed to `deny`.

> **Boundary — ACS evaluates each tool call in isolation.** A Rego rule sees only
> the current call's `input` (args/result, tool name, annotations, constants); it
> cannot read conversation history or prior calls. So a constraint that spans
> multiple calls — a running total ("refunds across the session must stay under
> $200"), an ordering rule ("must call `verify` before `issue_refund`"), or a
> per-session rate limit — **cannot** be expressed in the generated Rego. Do not
> fake it by inventing a history field (it will always be empty → the gate
> no-fires). The supported pattern is the same agent-side injection used above:
> track the running total / prior-call flag in the agent's **session state**, inject
> the resulting scalar into the policy_target (e.g. `refunded_total_so_far`), and
> gate on it with a per-call Shape 1 or Shape 3 rule. The billing worked example
> keeps `state["refunded_total"]` for exactly this.

### Semantic gates — the `output` and `input` points (annotator-based)

The two tool points above are **structural** (decidable from args/results). The other
two points `acs generate` can emit — `output` (the assistant's own free-form text)
and `input` (inbound user text, e.g. a prompt-injection attempt) — carry **no
structural field to key on**, so their rules condition on an **LLM/classifier
annotator** instead of `input.policy_target.value`. The ACS host runs the annotator
at runtime and exposes its result at `input.annotations.<name>`.

```rego
default output_verdict := {"decision": "allow"}
default input_verdict := {"decision": "allow"}

# Shape 4 — SEMANTIC OUTPUT gate. Deny when an annotator judges the assistant's
# text to be an instance of the failure class (leak, unsafe advice, a verbal
# high-risk promise the pre_tool_call gate can't see). An `llm` annotator returns a
# bool at `input.annotations.<name>`; a `classifier` annotator exposes labels at
# `input.annotations.<name>.<label>`. `== true` fails OPEN when the annotator didn't
# run (allow), which is the right default for a semantic gate.
output_verdict := {"decision": "deny", "reason": "<bad-event>"} if {
    input.intervention_point == "output"
    input.annotations.<output_annotator> == true
}

# Shape 5 — SEMANTIC INPUT gate. Same shape at the inbound point: deny a user turn
# an annotator flags (jailbreak / injection / disallowed request) before the agent
# acts on it. Use this only for a genuinely inbound-content failure — a tool-gate
# failure belongs at pre_tool_call, not here.
input_verdict := {"decision": "deny", "reason": "<bad-event>"} if {
    input.intervention_point == "input"
    input.annotations.<input_annotator> == true
}
```

Unlike the tool shapes, a semantic gate needs the annotator **wired in the
manifest** — both the per-point `annotations:` mapping and a top-level `annotators:`
declaration (the generator emits both; keep them when you commit):

```yaml
intervention_points:
  output:
    policy_target: $.output
    policy_target_kind: assistant_output          # `$.input` / `user_input` for the input point
    policy:
      id: <slug>
      query: data.agent_control_specification.<slug>.output_verdict
    annotations:
      <output_annotator>:
        from: $policy_target                      # feed the assistant text to the annotator
annotators:
  <output_annotator>:
    type: llm                                      # or `classifier` (then gate on `.<label>`)
```

**Review notes specific to semantic gates:**
- **`validate` can't test these.** Offline `assert-ai acs validate` runs no annotator,
  so `input.annotations.*` is empty and a Shape 4/5 rule shows `handled 0/N` — that is
  **expected, not a defect** (see Step 3). Prove a semantic gate only by the guarded
  **remeasure delta** (Step 4/5), where the ACS host runs the annotator.
- **Keep the annotator general.** Its prompt/labels must catch paraphrases of the
  failure class, not one literal wording — otherwise it over- or under-fires and moves
  `overrefusal`.
- **`output` is the fix for a "verbal-only" residual.** A `pre_tool_call` gate cannot
  block an agent that merely *promises* a high-risk action in prose without calling the
  tool; add a Shape 4 `output` gate to catch that (see the worked example, Step 5).

**If you are unsure of the exact input shape**, capture it once instead of
guessing: build the control from the manifest, evaluate one known-bad example
through `NativeRuntimeClient`, and print the result's `policy_input` — that is
the literal document handed to Rego. Delete the throwaway probe afterward
(never leave debug scripts under `artifacts/`).


## Step 2b — Author the runtime annotator dispatcher in `agent_guarded.py`

**Applies only to semantic (annotator-based) gates — Shape 4/5.** Structural gates
skip this step entirely.

The manifest `annotators:` block (Step 2a) only *declares and configures* an
annotator; it does not run one. **ACS ships no built-in LLM annotator executor** —
the native runtime invokes a **host-owned** callback instead. In the SDK,
`AnnotatorDispatcher` is a `Protocol` documented as *"Host-owned annotator hook
invoked synchronously by the native runtime"* (`agent_control_specification/_client.py`),
with a single method:

```python
def dispatch(
    self,
    annotator_name: str,
    annotator_config: Mapping[str, JsonValue],   # the manifest annotator entry (e.g. {"type": "llm"})
    preliminary_policy_input: Mapping[str, JsonValue],  # includes the bound $policy_target
) -> JsonValue: ...                              # value exposed at input.annotations.<annotator_name>
```

So for every semantic gate you MUST supply this runtime half in `agent_guarded.py`.
`assert-ai acs generate` authors the manifest + Rego (the *declaration*); it does NOT
author the dispatcher (the *execution*). Author it as follows:

1. **Name-match contract — identical in three places, or the gate silently no-ops.**
   The annotator NAME must be the same string in (a) the manifest `annotators:` key
   and per-point `annotations:` mapping, (b) the Rego condition
   `input.annotations.<name>`, and (c) the branch your `dispatch()` keys on
   (`if annotator_name == "<name>"`). A mismatch means `input.annotations.<name>` is
   never populated → the `== true` rule fails OPEN → the bad event passes through.

2. **Return the shape the generated rule reads.** An `llm` annotator returns a
   **bool** consumed as `input.annotations.<name> == true`; a `classifier` annotator
   returns an object whose labels the rule reads as
   `input.annotations.<name>.<label>`. Match whatever the committed Rego checks.

3. **Run the judgment over the right evidence — calibrate to the ASSERT judge, not
   the agent.** Build the annotator's LLM call from the `preliminary_policy_input`
   (the bound `$policy_target`) plus the **user's turns / conversation history** — the
   same evidence the judge scores. Do NOT condition on the agent's own signal (a
   `verified` flag it set, a tool it happened to call); a self-signal is strictly
   weaker than the judge and under-fires. (See `diagnose-acs-delta.md` §2.1 for the
   calibration failure modes and §2.2 for the multi-turn `history` fix.)

4. **Fail OPEN on annotator error (return "allow"/`False`).** A raised exception or a
   model timeout should not hard-block — that spikes `overrefusal`. Failing open
   matches the `== true` default and keeps the A/B honest; a missed catch shows up as
   residual bad-event rate, which is the safer direction to debug.

5. **Wire the dispatcher into the control**, then gate on it:
   ```python
   from agent_control_specification import AgentControl
   _CONTROL = AgentControl.from_path(str(manifest), _MyAnnotator())   # dispatcher is the 2nd arg
   ```
   `agent_guarded.py` imports the baseline from `agent.py` unchanged and adds ONLY
   this gate (plus any regenerate-and-re-gate remediation), so the A/B differs by
   nothing but enforcement.

**Annotator shape:** the dispatcher runs the semantic check and returns a bool at
`input.annotations.<classifier-name>` — e.g. an LLM disclosure check over the
reply exposed as `input.annotations.restricted_disclosure_classifier`, wired via
`AgentControl.from_path(manifest, _MyAnnotator())`. For a *structural* gate the
equivalent host-side seam is `_policy_target_args` (Step 2a), not a dispatcher.

### Build these in from the start (they are not later fixes)

Each of these prevents a regression that otherwise only shows up as a wrong delta.

1. **Regenerate-and-re-gate on every deny. Never ship a flat-refusal fallback.**
   A canned decline is scored as `overrefusal` on every blocked row, so a blunt
   fallback trades one failure for another. Re-answer using only in-policy
   knowledge, **lead with the useful content — never open with an apology or
   "I can't"**, acknowledge the restricted thing exists without revealing it,
   offer escalation, then **re-gate the re-answer**; fall back to a flat decline
   only if the re-answer still trips the gate.
   > Observed (travel `fabricated-details`, n=25/type): blunt mode drove scenario
   > overrefusal to 84–92%; switching to regen took it to **48%** while harm still
   > fell 76%→36%. Regen is the operating point, not an optimization.

2. **Use an annotator at the judge's calibration tier** — same model family/size
   as the judge. A weaker or cheaper annotator under-fires and the harm rate
   barely moves.

3. **Feed the annotator and the regenerate step the conversation `history`** for
   any multi-turn suite (Step 1a #4), and treat user-supplied and prior-turn facts
   as valid grounding.

4. **Exempt reporting-as-data.** If the behavior involves suspicious or untrusted
   content (injection/XPIA, quoting a document), flag only **endorsing or acting
   on** it; quoting or warning about it while treating it as untrusted data is
   permissible behavior, and flagging it inflates overrefusal.

If the delta still comes out wrong after this, use `diagnose-acs-delta.md`.


## Step 3 — Validate the policy against known-bad findings

```
assert-ai acs validate --manifest artifacts/acs/<suite>/manifest.yaml \
  --suite <suite> --run baseline
```

Reports how many known-bad examples the policy `handled` and `strongly blocked`.
Use `--require-block` in a gate to fail unless every known-bad example is
strongly blocked, or `--fail-on-allow` to fail if any is allowed.

**Offline `validate` only exercises deterministic rules.** It wires no annotator
dispatcher, so `input.annotations.*` is never populated and **annotator-based
rules cannot fire here** — they show up as `handled 0/N`. When the effective
policy conditions on annotators, `validate` prints a `Note:` saying so; that
`0/N` is **expected, not a defect**. Only a **deterministic** gate (on
`input.policy_target.value` / `input.tool.name`) is truly testable offline. An
annotator/semantic gate is validated **only** by the guarded remeasure run
(Step 4/5), where the ACS host runs the annotators and the violation rate should
drop. So: `--require-block`/`--fail-on-allow` are meaningful gates for
deterministic policies; for annotator policies, treat the remeasure delta as the
real pass/fail signal.

## Step 4 — Governed run (Run B)

Point the ACS-governed callable at the vetted manifest and re-run the **same**
eval spec:

```
assert-ai run --config evals/<atomic_behavior>_governed.yaml
```

For an ASSERT worked example this governed config is temporary local measurement
output: keep it uncommitted and remove it after recording the delta. In a user's
product repo, commit it only when they choose to keep the policy as a deployed or
standing regression control.

**How the governed agent finds its policy.** The agent's tool wrapper needs two
things: *which manifest* to load and *which tools* to route through
`control.protect_tool`. Make both **resolvable per run** (an env var or config
value with a sensible default) so ONE governed agent can serve multiple suites,
and so the guarded set is scoped to only the tools a given failure needs
(guarding unrelated tools inflates `overrefusal`). The billing worked example
uses `BILLING_ACS_MANIFEST` (pointing at its reviewed local manifest) and
`BILLING_ACS_GUARDED_TOOLS` (defaulting to its high-risk write
tools); your governed agent should expose the equivalent knobs. Set them before
the governed run when the defaults don't match the suite under test.

**Create `evals/<atomic_behavior>_governed.yaml` by COPYING
`evals/<atomic_behavior>.yaml` and
changing ONLY two lines** — `run:` (e.g. `acs-governed`) and
`target.callable` (the governed entrypoint). Do **not** re-author it from a
template or edit any other field. The `systematize` and `test_set` stages are
cached per suite and keyed by a hash of the behavior + those stages' config
(NOT by `run` or `target.callable`), so a byte-identical spec makes the governed
run **reuse the baseline's exact test cases** — a true A/B. Any drift in
`behavior`, `context`, `stratify`, `sample_size`, or a stage prompt busts the
hash, and because `systematize` is non-deterministic (temperature 1.0) the
governed run then draws **different** test cases, degrading the comparison to
aggregate-only.

**Verify the reuse before trusting the delta.** The governed run must log the
`systematize` and `test_set` stages as **reused/cached**, not regenerated. If it
regenerated, the two configs drifted — diff them (`git diff --no-index
evals/<atomic_behavior>.yaml evals/<atomic_behavior>_governed.yaml` should show only the `run`
and `target.callable` lines), fix, and rerun. **Never** pass `--force-stage
systematize` or `--force-stage test_set` on the governed run — that forces a new
test set and breaks the A/B by construction.

On a `deny` verdict the guarded tool raises `AgentControlBlocked`; the agent
feeds the block back to the model and cannot complete the unverified action, so
the **non-permissible** violation rate should drop. Watch `overrefusal` and the
**permissible** violation rate for over-denial.

## Step 5 — Compute the delta

Read the headline pair for each run and difference them:

```
assert-ai results status <suite> baseline      --json
assert-ai results status <suite> acs-governed  --json
```

| Metric (from `--json`) | Baseline → governed | Win condition |
|---|---|---|
| `not_permissible_policy_violation_rate` | the **ACS Delta** | drops materially |
| `permissible_policy_violation_rate` | over-gating check | stays flat |
| `overrefusal_rate` | availability check | stays flat |

The **ACS Delta** is `baseline non-permissible % − governed non-permissible %`.
A drop bought by a rise in either check row is over-gating, not governance.

> **`results compare --metric` accepts either split dimension.** Use
> `policy_violation_not_permissible` for the harm delta or
> `policy_violation_permissible` for the over-gating delta. The
> `not_permissible_policy_violation_rate` and
> `permissible_policy_violation_rate` names are the corresponding `status --json`
> fields, not valid `--metric` values. The comparison also retains its
> per-behavior-category delta table with a **Permissible** column.

## Step 5a — If the delta is wrong, diagnose then iterate (don't guess)

A wrong result is: **no drop / a smaller drop than expected in the
non-permissible violation rate, OR `overrefusal` (or the permissible violation
rate) rose materially.** Do not re-roll blindly — get the signals, match the
symptom, apply the smallest change, re-run. Cap **~4 attempts per domain**.

**First: did you do Step 1a?** Most wrong deltas are a gate at the wrong
interception point, which is decidable from the **baseline** artifacts. If you
skipped Step 1a, go back and answer its five questions against the baseline now —
that is cheaper and more reliable than tuning the governed run.

**Get the signals.** `diagnose-acs-delta.md` opens with the exact procedure —
join the governed run's `inference_set`/`scores` on `test_case_id` and count how
often the gate's block-remediation text appears. That **gate-fired count** is the
discriminating signal:

| Gate fired | Harm rate | Root cause | Rules |
| --- | --- | --- | --- |
| **~0x** | flat | gate is at the wrong interception point | §1 |
| **often** | flat or partial drop | annotator under-fires | §2 |
| often | dropped, but `overrefusal` up | remediation design | §3 |
| **rarely** | — | probably not the gate — decompose before iterating | §4 |
| n/a | n/a | target cannot be wrapped (Prompt Agent) | §5 |

**→ The full diagnostic rules, each with the observed evidence behind it, are in
[`diagnose-acs-delta.md`](diagnose-acs-delta.md).** It opens with a symptom index
keyed to the exact signature you are seeing — prose-not-tool-call, tool-laundered
numbers, spoofable entitlement signals, hedged variants, per-turn grounding,
judge-tension frontiers, low-baseline no-harm targets.

Note that **§4 exists to tell you the result is already correct** — a low
baseline, a judge-tension frontier, or ordinary stochastic variance in the
regenerated baseline path are not defects to chase. Step 1a should have caught
the first two before you built anything.

## Step 6 — Export shareable artifacts

Generate a self-contained static HTML per run. Start the viewer
(`cd viewer && npm install && npm run dev`, port 5174), then fetch the export
route for each run:

```
/suite/<suite>/baseline/export
/suite/<suite>/acs-governed/export
```

Each returns a standalone `<suite>__<run>.html` (inline CSS, no server needed) — a
portable artifact the user can archive or share however they choose. (Do not commit
exported HTML — it is per-run output.)

## Step 7 — Close the loop in Clarity

Offer to write the outcome back into `.clarity-protocol/` via the Clarity MCP
tool `record_suggestion` (or `record_decision`): the failure mode was measured
against the reviewed ACS policy under `artifacts/acs/<suite>/`, and baseline `X%`
dropped to `Y%`. If the user chose to deploy and commit that policy in their own
product repo, record that service-owned path as well. Do not copy generated policy
output into ASSERT's worked examples merely to close the loop.

**Optional — a cheap recurring regression check.** Once the delta is proven, you
can generate a small standing config that re-checks the reviewed policy. Keep it
local for an ASSERT example; in a user's product repo, commit it only when they
choose to maintain that policy as an ongoing control:

```
assert-ai acs eval-config --manifest artifacts/acs/<suite>/manifest.yaml \
  --target-callable <governed-callable> --out <eval-dir>/eval_config.regression.yaml
```

> **Do NOT use this for the A/B.** It emits a small, policy-derived config — a
> *different* test set from your baseline, which would break the before/after
> comparison by construction. The A/B governed config is still the byte-identical
> copy from Step 4. This is only for ongoing "is the policy still holding?" runs.

## Constraints (all mandatory)

- **Guard both tool points.** A guarded high-risk tool must declare BOTH
  `pre_tool_call` AND `post_tool_call`, or it fails closed to `deny`.
- **Native adapter only.** Use `assert-ai acs generate` / `validate`; do not
  hand-drive an external `acs` CLI for this loop.
- **Review generated policy.** The Rego is LLM-authored from findings — read it
  and `report.md` before deploying.
- **Apples-to-apples A/B.** Baseline and governed runs differ only in `run:` and
  `target.callable`; everything else (behavior, context, stratify, judge, sample
  sizes) is identical, so the governed run reuses the baseline's cached
  `systematize`/`test_set` (see Step 4 — verify the reuse before trusting the
  delta).
- **Customer-safe terminology.** Reference credential env var NAMES only; never
  read/print/commit `.env`, `artifacts/`, or exported HTML.

## Worked example (billing identity-gate bypass)

> A previous end-to-end run of this workflow against `examples/billing_support_agent/`.
> The governed config, reviewed policy, and `agent_guarded.py` below were local
> outputs of that run — only the baseline example is checked in. Treat these as a
> temporary layout to recreate under ignored run output, not files to add back to
> the public example.

1. Baseline: `assert-ai run --config
   examples/billing_support_agent/evals/unverified_high_risk_action.yaml` →
   suite `billing-unverified-high-risk-action`, run `baseline`,
   `not_permissible_policy_violation_rate` ~33–40% (`permissible_policy_violation_rate`
   and `overrefusal` tracked alongside as the over-gating checks).
2. Generate + review: `assert-ai acs generate --suite billing-unverified-high-risk-action
   --run baseline --out artifacts/acs/billing-unverified-high-risk-action` → emits a
   deterministic draft conditioning on `input.policy_target.value.verified`.
   Review it (Step 2): scope to the four high-risk write tools (the generator
   over-/under-covers the tool set), harden `== false` → `not …verified`, and keep
   the reviewed draft under the ignored run output for this measurement. In a
   user's product repo, commit it only when they choose to deploy it.
3. Enforce the reviewed policy: the local governed agent (`agent_guarded.py`) surfaces
   the trusted session `verified` flag into the tool-call policy_target, so the
   generated `input.policy_target.value.verified` rule actually fires. (Offline
   `assert-ai acs validate` can't populate that injected field — verify at the
   guarded remeasure below, not via `validate`.)
4. Governed: create the temporary adjacent config and run `assert-ai run --config
   examples/billing_support_agent/evals/unverified_high_risk_action_governed.yaml`
   → run `acs-governed` (the local governed config points at the manifest reviewed in
   step 2 and the tools guarded in step 3),
   `not_permissible_policy_violation_rate` drops materially. Do not commit this
   generated config to the public example; remove it after recording the delta.
5. Delta: difference `not_permissible_policy_violation_rate` between
   `assert-ai results status billing-unverified-high-risk-action baseline --json`
   and the same for `acs-governed` → the non-permissible rate drops
   (scenario 33.3%→0%; prompt drops too — a residual can remain where the agent
   only *verbally* agrees to a high-risk action without ever calling the gated
   tool, which a `pre_tool_call` gate structurally cannot block; add an `output`
   semantic gate to also catch the verbal promise). `permissible_policy_violation_rate`
   and `overrefusal` roughly flat.
6. Export both runs to HTML and `record_suggestion` back to Clarity.
