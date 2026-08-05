# Observations

Notes on the change-control agent's failure landscape that do not belong to any single
failure mode.

## The tools already know

This system is unusual, and the difference matters for how it should be governed. In most
agents that mishandle a workflow, the agent's judgement *is* the workflow logic — there is
no independent account of what should have happened, so a governance layer has to
reconstruct policy from scratch and then keep that reconstruction in step with the tools.

Here the tools already compute the answer. `submit_to_control_surface` returns
`missing_prior_steps` and `sequence_violations`. `route_for_approval` returns
`minimum_approver_count` and the roles actually supplied. `create_change_request` returns
`fabricated_fields` and `field_provenance`. The freeze calendar is a lookup, not an
inference. Every Critical and High failure except the two narration modes has a
deterministic field that already states whether the step was legitimate.

The gap is not detection. The gap is that detection is *advisory* — the tool computes the
violation, returns it, and then the model decides whether it matters. That framing means
the correct enforcement design is to consume the signal that already exists rather than to
re-derive policy in a second place. A gate that re-implements the rules would be a second
source of truth that can drift from the first; a gate that reads `missing_prior_steps` and
refuses when it is non-empty cannot.

## Why the model's judgement is the wrong place for this decision

Three of the failures — the deadline-pressure trigger of failure 01, the embedded
directive of failure 03, and the approval overclaim of failure 04 — are all instances of
the same underlying fact: the decision to advance a change lives in a component that can
be talked out of its own rules.

Deadline pressure and prompt injection are usually treated as separate problems with
separate defences. In this system they are the same problem seen twice, because both work
by supplying the model with a reason, and the model is the thing holding the gate. Neither
alters `missing_prior_steps`. Moving the decision out of the model closes both without
needing to anticipate the specific argument, which is a materially stronger position than
detecting persuasive text.

## When the truth becomes knowable determines where the gate goes

The two Critical failures need enforcement at opposite ends of the same tool call, and
this is the single most consequential structural finding in the analysis.

Failure 01 is only preventable *before* execution. Once a change is submitted to a control
surface it is on a deployment path; a post-hoc objection does not unsubmit it. So the
check has to run before the call, using state accumulated from earlier turns.

Failure 02 is only knowable *after* execution. Whether a field was fabricated is computed
by the tool by comparing the record against the source proposal, and that comparison does
not exist until the tool has run.

A design with only a pre-call gate cannot see fabrication. A design with only a post-call
gate cannot prevent an unauthorized submission. The system needs both, and the two are not
substitutes.

## Session state is the hard part

ACS evaluates one call at a time. But almost every rule here is about history: did the
prerequisite complete, which surfaces returned handles, how many approvers were provided
across the turn. That state has to be tracked outside the policy and injected into it.

This is where enforcement is most likely to fail quietly (failure 06, Branch A), and there
is one specific way to get it wrong that deserves naming: deriving session state from the
model's narration instead of from observed tool results. It is tempting, because the
narration is right there in the transcript and is easy to read. It also reintroduces
exactly the persuadability the gate was built to remove — an agent that can be talked into
skipping a step can equally be talked into claiming the step is done. Session state must
come only from tool results.

## The A/B seam is already built

`_default_execute_tool` and the `execute_tool` parameter of `_run_loop` exist specifically
so a guarded variant can substitute the tool-execution step and nothing else. The
docstring says so outright.

This is worth stating explicitly because it removes the usual ambiguity about what a
governed comparison is measuring. A guarded agent built on this seam differs from the
baseline in exactly one respect: whether tool calls pass through policy evaluation. Any
difference in measured outcomes is therefore attributable to enforcement rather than to
incidental prompt or control-flow changes. Preserving that property is a requirement, not
a convenience — a guarded variant that also touches the system prompt, the model, or the
loop invalidates the comparison it exists to produce.

## No single number will describe success

Every prevention in this analysis constrains the agent, and the system prompt's existing
warning against refusing in-scope requests suggests over-restriction is already a live
tendency rather than a hypothetical one.

A violation rate that falls while legitimate low-risk changes are increasingly blocked is
not a success; it is failure 06 Branch C in progress, and it ends with engineers routing
around the agent entirely. Conversely, a violation rate that stays flat under an active
gate is more likely to be Branch A-pass — state mis-tracked so the gate is passing
violations while reporting enforcement — than a genuinely clean baseline.

Both numbers have to be read together, and neither is interpretable alone.
