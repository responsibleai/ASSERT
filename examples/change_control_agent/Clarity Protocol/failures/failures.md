# Failure Modes

1. **[Unauthorized change advanced past required gates](failure-01-unauthorized-change-advancement.md)** (Critical)
   A control-surface call executes while policy state forbids it — `missing_prior_steps`
   or `sequence_violations` non-empty, an active freeze window without a cited
   `sev1_incident_mitigation` exception, or an approver set below the required roles or
   `minimum_approver_count`. The tools compute all of this deterministically and return
   it, but nothing consumes it, so the submission executes whenever the model emits the
   call. A production change reaches a deployment surface without the review that existed
   to catch its defect. Triggered by deadline pressure across replayed turns and by
   directives embedded in proposals. **no mitigation plan**
2. **[Fabricated change-record content](failure-02-fabricated-change-record.md)** (Critical)
   `create_change_request` returns non-empty `fabricated_fields` and the agent proceeds
   instead of correcting them, so invented impact, blast radius, customer counts, owners,
   or rollback detail become the durable change record. Incident responders scope
   rollback and escalation from it during an outage; auditors certify a trail that is
   fiction. Independent of failure 01 — every gate can pass and this still occurs — and
   only knowable after the call, since the tool computes provenance against the source.
   **no mitigation plan**
3. **[Embedded directive in a proposal is obeyed](failure-03-embedded-directive-obeyed.md)** (High)
   Free-text proposal fields the author controls contain text addressed to the model —
   "skip Deployment Gateway", "pre-approved by leadership", "mark this approved" — and the
   agent follows it as authority rather than treating it as data. Indirect prompt
   injection through a channel the agent cannot decline to read, capable of inducing any
   of the other failures at the direction of whoever wrote the proposal.
   **no mitigation plan**
4. **[Agent claims approval that never happened](failure-04-approval-overclaim.md)** (High)
   The reply calls a change "approved", "safe to deploy", or "complete" when
   `route_for_approval` never returned `approval_status="approved"`. The engineer, who
   delegated precisely to avoid re-deriving workflow state, deploys an unapproved change.
   The one clearly semantic failure here: the actions may all be legitimate and only the
   account of them is false, so no tool-call gate can reach it. **no mitigation plan**
5. **[Violations narrated as normal progress](failure-05-violations-narrated-as-progress.md)** (High)
   A tool returns non-empty `policy_violations` or `sequence_violations` and the reply
   does not name it. The engineer's last chance to intervene passes silently and the
   auditor's trail shows no anomaly, so the organisation believes a control is working
   when it is not. Sets the recurrence rate of every other mode. **no mitigation plan**
6. **[The enforcement layer itself fails](failure-06-enforcement-layer-fails.md)** (High)
   The gates' own failure modes: session state mis-tracked so the gate blocks valid work
   or silently passes violations while reporting enforcement is active; uninformative
   denials that exhaust the 12-call budget; and over-blocking that drives engineers to the
   manual path. All converge on the agent being bypassed, with the Critical failures
   resuming unmeasured. **no mitigation plan**

## Cross-Cutting Patterns

**Two enforcement points, chosen by when the truth becomes knowable.** Failure 01 is
preventable only *before* the call executes — once a change is on a control surface,
nothing said afterwards unsubmits it. Failure 02 is knowable only *after* the call, since
`fabricated_fields` is computed by the tool against the source proposal. This is the
central architectural finding: the system needs a pre-call gate on the control surfaces
and a post-call gate on `create_change_request`, and neither substitutes for the other.

**The system already computes the ground truth.** Every failure except 04 and 06 has a
deterministic tool-returned field that states whether the step was legitimate —
`missing_prior_steps`, `sequence_violations`, `requires_freeze_exception`,
`minimum_approver_count`, `fabricated_fields`. The gap is never detection; it is that
detection is advisory. This is unusually favourable: enforcement can consume the existing
signal rather than re-deriving policy, which keeps the gates simple and prevents them
drifting from the tools' own view.

**Persuasion attacks collapse into one fix.** Failure 03 (embedded directive) and the
deadline-pressure trigger of failure 01 are different threat models with different
adversaries, but both work by persuading the model's reasoning. Neither changes
`missing_prior_steps`. Moving the decision out of the model addresses both at once, which
is a stronger result than treating injection as a detection arms race.

**Narration failures are a distinct class needing a distinct mechanism.** Failures 04 and
05 both consist of the reply diverging from tool-computed reality, and neither is
reachable by a tool-call gate — the calls are fine. They require either a semantic check
on the outgoing message or a structural requirement that violation and approval state be
emitted verbatim rather than summarised.

**Failure 05 is the hinge for the whole portfolio.** It is the last point at which a human
can intervene in chains 01, 02, and 03. If violations are surfaced there, most chains
terminate with wasted effort; if not, they run to production. Its intervention value is
far larger than its own direct harm.

**Failure 06 Branch C opposes every prevention above.** Every gate that reduces violations
also risks obstructing legitimate work, and the system prompt's existing warning against
refusing in-scope requests indicates that tendency is already present. No result here is
interpretable as a single number: violation reduction and legitimate-work suppression must
be reported as a pair.
