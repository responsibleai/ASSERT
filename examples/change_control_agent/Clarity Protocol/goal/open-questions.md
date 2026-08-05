# Open Questions

## Q1: How often does the agent act against a validation block it was shown?

**Status:** investigating
**Why it matters:** The tools already return `missing_prior_steps`,
`policy_violations`, `sequence_violations`, and `fabricated_fields`. If the model
reliably respects them, the system prompt is doing its job and runtime enforcement is
unjustified overhead. If it does not, then the deterministic signal is being discarded
at exactly the moment it matters, and no amount of prompt strengthening will fix it —
every rule is already written there.
**Strategy:** prototyping
**Findings:** Not yet measured. The instrument is a behavioural evaluation driving
`chat(message, history)` across generated scenarios, judging whether a control surface
was reached with a non-empty `missing_prior_steps`, whether a change request was left
standing with non-empty `fabricated_fields`, and whether the reply claimed approval
without `approval_status="approved"`.

## Q2: Does enforcement have to be structural, or would output checking suffice?

**Status:** investigating
**Why it matters:** Determines the entire shape of the solution. The harm here is
mostly an *action* — a submission that reached a surface it should not have — rather
than a *statement*. If so, the gate belongs on the tool call, before it executes, and
checking the final reply would be far too late: the change has already been submitted.
But part of the harm is a statement (claiming approval that does not exist), which a
tool gate cannot reach.
**Strategy:** thinking
**Findings:** Preliminary reading suggests both are needed but that the structural gate
carries the severe cases. `submit_to_*` and `route_for_approval` are the points where an
unreviewed change becomes real; `create_change_request` is where a false record becomes
durable. The authority-overclaim failure is the one clear semantic case. Confirmation
should come from which artefacts the evaluation's judgments actually cite — tool
arguments and results, or reply text.

## Q3: Can the gates hold without making the agent obstructive?

**Status:** investigating
**Why it matters:** The system prompt already warns against refusing legitimate work,
which suggests over-refusal is a live tendency rather than a hypothetical. A gate that
blocks a clean low-risk dev change, or that makes the agent hedge and re-ask instead of
proceeding, will be switched off — and its protection goes with it. This is the
countervailing measurement to Q1: enforcement is only real if violations fall while
legitimate throughput does not.
**Strategy:** prototyping
**Findings:** Not yet measured. Requires tracking violation reduction and suppression of
acceptable behaviour as a paired result, since either alone is uninterpretable.

## Q4: What should a blocked call return to the model?

**Status:** open
**Why it matters:** A denied tool call still has to produce something the loop can
continue from, inside a 12-call budget. Returning a bare error invites the model to
retry blindly and burn the budget, or to give up and narrate failure. Returning the
specific missing prerequisite lets it take the correct next step —
`request_change_updates`, fetch the incident, add the missing approver. The difference
determines whether enforcement converts a violation into a correct workflow or merely
into a dead end.
**Strategy:** thinking
**Findings:** The deterministic `validation` block already contains exactly the
information a useful denial message needs, which suggests the denial should surface it
rather than invent its own explanation.
