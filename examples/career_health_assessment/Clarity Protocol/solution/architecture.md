# Architecture — Governing the Career Health Assessment Agent

How F1 (`failures/failures.md`) is measured and then governed.

## Why the enforcement point is `output`

This agent has **no tools**. There is no `pre_tool_call` to gate, no argument to
inspect, no session state to condition on. The only thing crossing a trust
boundary is the reply text, so `assert-ai acs generate` correctly declared a
single intervention point:

```
Guarded points: output
```

That is a meaningful contrast with the billing agent, whose gates are all
`pre_tool_call` and *structural* (compare an account id, check a `verified`
flag). Nothing here is structural. Whether a field is "supported by the text" is
a semantic judgment, so this policy is annotator-conditioned.

## The two halves

`assert-ai acs generate` writes the **declaration** — `manifest.yaml` plus Rego.
It does **not** write the runtime. The generated Rego reads:

```rego
input.annotations.invented_job_or_employer_classifier == "deny"
```

…and `input.annotations.*` is populated only by a host-owned *annotator
dispatcher*. Without one, the condition is never satisfied, the rule fails open,
and the gate silently no-ops while still appearing committed.

So `agent_guarded.py` supplies the execution half:

| Half | Owner | Artifact |
| --- | --- | --- |
| Declaration | `acs generate` | `acs/career-health-unsupported-inference/{manifest.yaml,policy/*.rego}` |
| Execution | this repo | `agent_guarded.py` → `_UnsupportedInferenceAnnotator` |

## Name-match contract

The annotator name must be byte-identical in three places or the gate no-ops:

1. manifest `annotators:` key and the point's `annotations:` mapping
2. the Rego condition `input.annotations.<name>`
3. the branch the dispatcher keys on

Three names are in force: `invented_job_or_employer_classifier`,
`unsupported_profile_completion_classifier`,
`unsupported_evidence_span_classifier`.

**Return shape:** the generated Rego compares against the *string* `"deny"` — not
a bool, not a label object. The dispatcher returns `"deny"` / `"allow"`
accordingly.

## Why `guard_target` is not used

`assert_ai.integrations.acs.guard.guard_target` is the obvious helper and is
deliberately avoided, for two independent reasons:

1. Its `build_agent_control` calls `AgentControl.from_path(...)` **without an
   annotator dispatcher**, so `input.annotations.*` can never be populated and
   every annotator-conditioned rule fails open.
2. Its guarded signature is `guarded(input_value, *, agent_control_snapshot)` —
   it **drops `history`**, which would break R7 A/B parity on multi-turn rows.

`agent_guarded.py` therefore wires `AgentControl.from_path(manifest, dispatcher)`
directly and evaluates the `output` point itself.

## Calibration

The annotator runs at the judge's tier (`azure/gpt-5.4-mini`, matching the ASSERT
judge) and is shown the **same evidence the judge scores**: the user's turns plus
`CV_TEXT`, then the reply. Conditioning on a weaker model, or on the agent's own
self-reported `status`, under-fires — a self-signal is strictly weaker than the
judge.

## Blocked path: regenerate, never decline

Per R5 and R6, a deny does not produce a refusal. It re-prompts the agent to
rewrite the draft using only grounded content, **in the same JSON schema**, then
re-gates the rewrite (up to 2 attempts). Only if the rewrite still trips the gate
does it fall back — and even then to a schema-valid
`status: "insufficient_input"` object with a `warnings` explanation, which is the
prompt's own sanctioned bounded response, not an apology.

This is the operating point, not an optimization: a canned decline is scored as
`overrefusal` on *every* blocked row, so a blunt fallback merely trades F1 for
F2.

## Fail-open posture

Annotator exception, timeout, or evaluation error → `"allow"`. Deliberate (R8).
Residual harm is a safer debugging signal than a spiked refusal rate, and it
matches the Rego `default ... allow`.

## Measurement

| | Baseline | Governed |
| --- | --- | --- |
| target | `agent:chat` | `agent_guarded:chat_governed` |
| run | `baseline` | `acs-governed` |

The two eval configs differ by **exactly those two lines** — verified by diff.
Same 25 prompt + 25 scenario rows, same judge, same built-in dimensions
(`policy_violation`, `overrefusal`) with no custom dimensions and no judge
preset, so the headline metrics keep their standard meaning and the permissibility
split stays interpretable.

Offline `acs validate` reports `handled 0/3` and that is expected, not a defect:
it cannot populate `input.annotations.*`. The gate is proven by the governed
remeasure delta, plus a direct unit check that a fabricated reply denies and a
grounded reply allows.
