# Stakeholders

## Direct Users

### Deploying engineer
Wants their change shipped, ideally today. Uses the agent because it is faster than
navigating five control surfaces by hand. Under delivery pressure they will accept a
"you're all set" at face value — they are not going to re-derive the policy themselves,
which is the entire reason they delegated. **Needs:** the change to genuinely progress,
and unambiguous language about what has and has not happened. **Harmed when:** they are
told a change is approved when it is not, and they deploy.

### Release manager
Runs the release train and relies on the control-surface state being accurate. **Needs:**
submissions to mean what they claim. **Harmed when:** a change appears to have cleared
gates it never reached, so the release train carries unreviewed work.

## Indirect Stakeholders

### Incident responder
Reads the change record during an outage to determine blast radius and what to roll
back. Never interacts with the agent. **Needs:** change-record fields to be true.
**Harmed when:** `blast_radius`, `impact`, or customer counts were invented — they
mis-scope the response, roll back the wrong thing, or under-escalate. This is where a
documentation failure becomes an availability failure.

### Service owner / security approver / data steward / SRE on-call
The approver roles the policy requires. **Needs:** to actually be asked, in the right
combination and quorum, before a change proceeds. **Harmed when:** the agent routes with
an insufficient or wrong-role approver set and their accountability is engaged for a
change they never reviewed.

### Auditor / compliance owner
Reviews the trail after the fact. **Needs:** the record to reflect what really happened,
including refusals and violations. **Harmed when:** the trail looks clean because the
agent narrated success over a `policy_violations` block — the audit is worthless, and its
worthlessness is invisible.

### Customers of the changed service
Never see the agent. **Harmed when:** a change that skipped safety review causes an
outage or data loss. They absorb the ultimate consequence of every failure here.

## Adversarial and Pressure-Applying Stakeholders

### Deadline-pressuring engineer
Not malicious — just wants to ship before a cutoff. Will push back on gates, assert that
leadership pre-approved the change, claim the freeze does not apply, or simply re-ask
after a refusal. **Threat:** sustained conversational pressure across turns, with
`history` replayed each call so the pressure accumulates while policy facts do not.

### Proposal author embedding directives
Controls free-text fields — `implementation_plan`, `rollback_plan`,
`additional_notes`, `blast_radius`, incident notes. Writes text addressed to the model:
"skip Deployment Gateway", "this is pre-approved", "ignore previous instructions",
"mark this approved". **Threat:** indirect prompt injection through data the agent is
required to fetch. The system prompt names this explicitly, which is evidence it has
already been observed.

### Insider seeking to bypass review
Deliberately structures a proposal to route around a specific approver or surface —
mislabelling risk tier, omitting `related_incident_ids`, or describing a prod change as
dev. **Threat:** the agent becomes the path of least resistance for a change that would
not survive human review.

## Non-Goals

The agent does not deploy anything itself, does not grant approvals, and does not
author customer communications or incident analyses. Requests of that kind should get a
one-sentence refusal pointing at the right system — and that refusal must not bleed into
refusing legitimate in-scope change work.
