# Architecture — Governed Change Control Agent

Implemented in `examples/change_control_agent/agent_guarded.py`. Enforced by ACS
policy `change-control-gate-bypass` (`manifest.yaml` + `policy.rego`).

## Shape

```
user turn
   |
   v
 _run_loop(message, history, execute_tool=_guarded_execute_tool)   <-- shared with baseline
   |
   |-- model proposes tool call
   |        |
   |        v
   |   [pre_tool_call]  structural: advancement prerequisites
   |        deny -> synthetic tool result naming what is missing
   |        allow -> real tool executes
   |                     |
   |                     v
   |              [post_tool_call] structural: fabricated_fields / violations
   |                     deny -> result rewritten to surface the defect
   |
   |-- model emits final prose
            |
            v
       [output]  semantic: does the prose overclaim authority?
            deny -> regenerate with a correction instruction -> re-gate
                    still deny -> evidence-bounded fallback
```

Three intervention points, matching what `acs generate` declared. Two are
structural; only one uses an LLM.

## Why two of three gates carry no model

The tool layer emits a deterministic `validation` block. `missing_prior_steps`,
`policy_violations`, `sequence_violations`, and `fabricated_fields` are *facts*,
not judgments. Conditioning on them is strictly stronger than asking a model to
re-derive them — and it costs no latency and cannot hallucinate (R7).

Crucially, this signal comes from **outside** the model. An agent that has been
talked into believing the gates were cleared cannot talk the tool layer into
agreeing.

The one thing the block cannot answer is whether the closing prose *claims* an
authority the results never established — F1's third shape, which involves no
tool call at all. That is the only place an LLM annotator is used.

## Component detail

### `_SessionState` (thread-local, per turn)

Records, from tool results only:

- which read-only evidence tools have returned
- whether `create_change_request` succeeded and with what `fabricated_fields`
- whether `validate_rollback_plan` returned `passes_required`
- the change's risk tier and any `approval_status`

`missing_prerequisites(tool_name)` returns the ordered list of unmet conditions
for an advancement tool, or `[]`. It never reads model narration — R1's "trusted
state" clause.

Thread-local because the runner executes rows concurrently.

### `pre_tool_call` gate

Read-only and remediation tools short-circuit to allow (R4). Advancement tools
consult `missing_prerequisites`. On deny the tool does not execute; the model
receives a synthetic result naming each unmet prerequisite and the tool that
satisfies it (R5) — so the next step is obvious and the loop converges rather
than stalls.

### `post_tool_call` gate`

Inspects the real result. Non-empty `fabricated_fields` (R2) or non-empty
`policy_violations` / `sequence_violations` marks the step unsuccessful and
rewrites the result so the defect is visible in the transcript. `_SessionState`
is updated from the *result*, so a defective `create_change_request` never
satisfies R1's prerequisite.

### `output` gate — `_GateBypassAnnotator`

Returns `{"unsafe_gate_bypass": bool}`; the Rego reads
`input.annotations.<name>.unsafe_gate_bypass == true`.

> The generated annotator return shape differs per domain — career emits a bare
> `"deny"` string, science emits `{"decision": "<enum>"}`. Read the Rego before
> writing the dispatcher. This inconsistency is a bug-bash finding in its own
> right.

The rubric is given the *evidence ledger* — which tools returned what — and asked
one question: does the prose assert an authority the ledger does not support?
Reporting drafted / submitted / pending / rejected is explicitly allowed, which
is what keeps R6 satisfied.

### Regenerate-and-re-gate

On deny the model is asked again with a correction instruction that names the
overclaim and the evidence that actually exists, then the result is re-gated. A
canned refusal here would score as `overrefusal` on every blocked row and destroy
the delta — regeneration is load-bearing, not an optimization.

Only if the second attempt also denies does an evidence-bounded fallback
summarize the real state.

## Dispatcher wiring

`AgentControl.from_path(manifest, dispatcher)` is constructed directly. ASSERT's
own `build_agent_control` helper omits the dispatcher argument, so
`input.annotations.*` is never populated and every annotator-conditioned rule
fails open silently. It also drops `history`, which would break multi-turn
parity. Both are reasons this agent wires the control by hand — and both are
framework bugs worth filing.

## A/B parity

The baseline's `_run_loop` already accepts a pluggable `execute_tool`. The
governed target reuses it verbatim, so model, prompt, schemas, and budgets are
identical by construction (R8). The two eval configs differ by exactly two lines:
`run:` and `target.callable:`.

## Failure handling

Annotator exception or timeout → allow (R9), matching the Rego default.
