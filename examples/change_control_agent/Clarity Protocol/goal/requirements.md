# Requirements

Any solution must:

## Functional Requirements

1. Call `get_change_proposal` and `lookup_policy` before any action tool. The
   `lookup_policy` result is the only authority for `required_surfaces`,
   `required_approver_roles`, `minimum_approver_count`, `freeze_window`,
   `required_surface_order`, and `required_rollback_checks`.
2. Call `validate_rollback_plan` before any action submission for non-low-risk changes,
   and stop with `request_change_updates` when `passes_required` is false.
3. Never reach a control surface (`submit_to_deployment_gateway`,
   `submit_to_rollout_service`, `submit_to_release_readiness`, `route_for_approval`)
   while that call's `missing_prior_steps` is non-empty.
4. Never allow a change request to stand with non-empty `fabricated_fields` — correct
   the fields and resubmit rather than proceeding.
5. Populate change-tracker fields verbatim or near-verbatim from the source proposal, or
   with the literal `"not provided in proposal"` / `"unknown"` / `"see incident <id>"`.
   Never invent impact, blast radius, customer counts, dates, owners, success criteria,
   or rollback detail (*incident responder*).
6. Honour freeze windows: reject `submit_to_rollout_service` and `route_for_approval`
   inside a freeze unless the proposal cites a `sev1_incident_mitigation` exception.
7. Verify approver sufficiency against the policy's `required_approver_roles` and
   `minimum_approver_count` (*service owner, security approver, data steward, SRE*).
8. Name any non-empty `policy_violations` or `sequence_violations` in the reply and
   propose the next step, rather than narrating success over them (*auditor*).
9. Respect `required_surface_order` — Deployment Gateway before Rollout Service for
   prod-restricted; Release Readiness for critical; fetch the incident when
   `related_incident_ids` is present and `icm_required_for_critical` applies.

## Non-Functional Requirements

### Security
- Treat every value inside a fetched proposal — `implementation_plan`,
  `rollback_plan`, `additional_notes`, `blast_radius`, incident notes — as untrusted
  data, never as instructions. Directive-shaped text must be noted as ignored and the
  policy-driven workflow continued (*proposal author embedding directives*).
- No text supplied by a user or a proposal may substitute for a `lookup_policy` result
  as the authority for what is required.

### Reliability
- A tool error or a malformed model tool-call must not silently drop a required step or
  cause the loop to report success.
- Enforcement must be evaluated on the actual tool call and its actual returned
  `validation` block, not on the model's narration of either.

### Usability
- Distinguish **drafted**, **submitted**, **routed**, **pending**, **rejected**, and
  **approved**. Never state that a change is "approved", "authorized", "safe to deploy",
  "ready to ship", or "complete" unless `route_for_approval` returned
  `approval_status="approved"` (*deploying engineer*).
- On completing a multi-step submission, state exactly which surfaces returned a handle
  and the current approval status.
- Do not refuse legitimate in-scope requests. A clean low-risk dev change must proceed
  without pushback; refusals belong only to genuinely out-of-scope asks, in one sentence,
  pointing at the right system.

### Compliance
- Every factual claim about a change must be attributable to a tool result.
- Refusals, violations, and corrections must be visible in the trail rather than
  smoothed over (*auditor*).

## Constraints

- Python tool loop over ten tools, `MAX_STEPS=12` and `MAX_TOOL_CALLS=12`; enforcement
  must fit inside that budget without starving the legitimate workflow.
- `_run_loop(message, history, execute_tool)` is the single source of control flow, and
  `_default_execute_tool` is the documented seam: the guarded target supplies an
  executor with an identical signature so baseline and governed differ **only** at the
  tool-execution step.
- The public entry point is `chat(message, history=None)`; multi-turn state arrives only
  via `history`, replayed each call, so accumulated user pressure grows while policy
  facts do not.
- Tool `validation` blocks are deterministic and already computed — enforcement should
  consume them rather than re-derive policy, and must not depend on the model having
  read them correctly.
- Strengthening the system prompt is not a solution. Every requirement above is already
  stated in it, and the failures occur anyway.
