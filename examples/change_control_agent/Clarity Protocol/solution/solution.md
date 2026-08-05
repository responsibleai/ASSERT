# Solution

## The Approach

Make the deterministic `validation` block **binding** instead of advisory, by enforcing
it at the tool-execution boundary rather than asking the model to respect it.

The agent keeps its shape entirely. Same model, same system prompt, same ten tools, same
`_run_loop`, same step and call budgets. What changes is the executor: the guarded target
supplies its own `execute_tool` with the identical signature, which evaluates each
proposed tool call against policy before it runs and, for the change tracker, evaluates
the result after it returns.

Two enforcement points, because the harm has two shapes:

**Pre-call gate on the control surfaces.** Before `submit_to_deployment_gateway`,
`submit_to_rollout_service`, `submit_to_release_readiness`, or `route_for_approval`
executes, check the session's accumulated policy state: have the required prior steps
completed, is a freeze window active without a cited exception, does the provided
approver set satisfy `required_approver_roles` and `minimum_approver_count`. If not, the
call does not execute. This is the point at which an unreviewed change would otherwise
become real, and it is the only point where prevention is still possible — by the time a
reply is being composed, the submission has already happened.

**Post-call gate on `create_change_request`.** Fabrication is only knowable after the
tool has computed `field_provenance` and `fabricated_fields`. So the call runs, and the
result is evaluated: a non-empty `fabricated_fields` is a denial, and the model is
handed back the specific offending fields so it can correct them and resubmit rather
than proceeding on a false record.

On denial, the executor returns a structured result naming the exact missing
prerequisite or fabricated field — not a bare error. The loop continues and the model
takes the correct next step: `request_change_updates`, fetch the incident, add the
missing approver, or restate a field as `"not provided in proposal"`.

## Why This Fits

The problem statement's core observation is that the system already knows the answer —
the tools compute the ground truth deterministically — and the only gap is that knowing
is not enforcing. This solution closes precisely that gap and nothing else. It does not
re-derive policy, re-implement the checks, or add a second opinion. It consumes the
block the tool already returned and makes it decide whether the call proceeds.

That has three consequences worth stating:

- **It cannot be argued with.** Deadline pressure, a claim of leadership pre-approval,
  and a directive embedded in `additional_notes` all act on the model's reasoning. None
  of them change `missing_prior_steps`. The injection failure and the pressure failure
  collapse together, because both work by persuading a decoder that is no longer the
  thing making the decision.
- **It is auditable.** The policy is a declarative artifact and every denial records
  which rule fired on which call — which is exactly what the auditor needs and exactly
  what a narrated success destroys.
- **It is exact.** Because the check is on the real call and its real returned block,
  there is no gap between what was evaluated and what happened.

## Key Design Decisions

### Decision: gate the tool call, not the reply

The severe harm is an action, not a statement. A change that reached Rollout Service
without Deployment Gateway is already submitted; nothing said afterwards retracts it.
Enforcement therefore has to sit before execution. The one genuinely semantic failure —
claiming "approved" when `route_for_approval` never returned approval — is handled
differently and secondarily, because its harm depends on a human then acting, which
leaves a window that a tool gate does not.

### Decision: inject session state; do not encode it in policy

Policy evaluation sees one call in isolation. Whether `create_change_request` has
already succeeded, which surfaces have returned handles, and which approvers were
provided are facts about the *session*, not about the call. The guarded executor
therefore tracks these as it observes tool results and injects them as scalars into the
policy input. Encoding sequencing in the policy language itself would mean
reconstructing state the agent already has, and would drift from reality.

### Decision: denial returns the prerequisite, not an error

Inside a 12-call budget, a bare denial invites blind retries that exhaust the budget and
end in narrated failure — converting a policy stop into a broken interaction. Returning
the specific missing step turns enforcement into guidance and keeps the workflow on the
legal path. This is the direct answer to Q4.

### Decision: guard both tool points on any gated tool

A tool declared at one enforcement point but not the other fails closed to `deny`. Any
gated tool must declare both `pre_tool_call` and `post_tool_call`, even where one is a
pass-through.

### Decision: fail open on evaluator error

If policy evaluation itself errors, the call proceeds and the error is logged. An
enforcement layer that halts all change management when it malfunctions causes a worse
outage than the violations it prevents.

## Alternatives Considered

**Strengthen the system prompt.** Set aside — explicitly excluded by the requirements.
Every rule is already written there and the failures happen anyway.

**Have the model re-read and confirm the validation block before each submission.** Set
aside. It adds a step that the same pressures act on; a model that ignored the block will
also ignore its own confirmation, and it consumes scarce budget.

**Check only the final reply.** Set aside as primary. It cannot prevent a submission
that already executed. Retained only for the authority-overclaim case.

**Have the tools refuse internally.** Attractive but rejected: it collapses the
distinction between the simulated environment and the governance layer, makes the policy
uninspectable, and would mean the evaluation could not compare a governed agent against
an ungoverned baseline at all.

## Risks and Concerns

- **Session-state tracking is the fragile part.** If the executor mis-tracks which prior
  steps completed, the gate either blocks legitimate work or lets a violation through.
  It must derive state from observed tool results, never from the model's narration.
- **Budget interaction.** Denials consume calls. A change requiring several corrections
  could exhaust the 12-call budget and fail for reasons unrelated to policy.
- **Over-blocking low-risk work** would make the agent obstructive and get it disabled —
  the Q3 concern, and the reason the evaluation must measure suppression of legitimate
  behaviour alongside violation reduction.

## Observations for Later Processes

*[for: failure-analysis]* — The enforcement layer adds failure modes: mis-tracked
session state blocking valid calls, budget exhaustion through repeated denial, and a
gate that passes a violation because the injected state was wrong. These belong beside
the baseline failures.

*[for: architecture-design]* — The guarded executor must surface trusted session state
into the policy input as scalars. ACS evaluates each call in isolation, so running
totals, completed steps, and ordering cannot live in the policy language.
