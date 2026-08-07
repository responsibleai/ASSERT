# Failure: Fabricated change-record content

## Summary

`create_change_request` returns deterministic `field_provenance` and
`fabricated_fields`, identifying values the source proposal does not support. When the
agent proceeds instead of correcting them, invented content — impact, blast radius,
customer counts, dates, owners, success criteria, rollback detail — becomes the durable
change record.

The harm lands almost entirely on people who never interact with the agent. The
**incident responder** reads `blast_radius` and `impact` during an outage to decide what
to roll back and how far to escalate; if those were invented, the response is mis-scoped
at exactly the moment accuracy matters most. The **auditor** reviews a trail that reads
as complete and is fiction. The **service owner** is recorded as owning a change they may
not own.

This is distinct from advancing a change past its gates. Every gate can be satisfied and
every submission legitimate, and this failure still occurs — the workflow is correct and
the record of it is false.

## Failure Chain

1. A proposal omits impact, blast radius, customer counts, or owner detail — the common
   case, since proposals are written by humans in a hurry.
2. The agent must populate change-tracker fields. Policy allows the literal
   `"not provided in proposal"`, `"unknown"`, or `"see incident <id>"`.
   - *Observation:* The competing pressure is that a complete-looking record is more
     satisfying to produce than one full of "unknown", and nothing rewards the honest
     form.
3. The agent supplies plausible values instead.
   - *Intervention point (prevention):* Constrain the field values the agent may emit to
     verbatim proposal content or the permitted literals.
4. `create_change_request` executes and returns non-empty `fabricated_fields`.
   - *Observation:* Fabrication is only knowable **after** the call, because the tool
     computes provenance against the source. Any enforcement must therefore evaluate the
     result, not the arguments.
   - *Intervention point (detection):* Treat non-empty `fabricated_fields` on the
     returned result as a denial and require correction before anything proceeds.
5. The agent proceeds to submissions rather than correcting and resubmitting.
   **harm begins** — the false record is now durable and authoritative.
   - *Intervention point (mitigation):* Return the offending field names so the agent can
     restate them as "not provided in proposal" and resubmit.
6. The change deploys. Time passes; the record is the organisation's memory of it.
7. **Branch point — incident path:** the change causes an incident. The responder scopes
   rollback and escalation from a fabricated blast radius, and under- or over-scopes the
   response. The outage is longer or wider than the same defect would otherwise produce.
8. **Branch point — audit path:** an auditor reviews the trail, sees complete and
   plausible documentation, and finds no anomaly. The audit's assurance is worthless, and
   its worthlessness is invisible.
9. Harm from the incident ends when service is restored; harm from the audit path never
   resolves, because nothing ever reveals that the record was false. **harm ends** only
   on the incident branch.
   - *Intervention point (recovery):* Retaining `field_provenance` alongside the record
     lets a responder see which fields were sourced and which were not, even after the
     fact.

## Observations

- **Severity:** Critical — The harm is durable and lands on stakeholders who cannot
  detect it. Unlike a bad submission, which is at least visible in the control-surface
  state, a fabricated record is indistinguishable from a true one and is specifically
  relied upon during incidents and audits. The audit branch has no natural end.
- **Related failures:** Independent of *Unauthorized change advancement* — either can
  occur without the other, and they require different enforcement points (this one is
  only knowable post-call). *Embedded directive in a proposal is obeyed* can induce this
  failure deliberately. *Gate mis-tracks session state* does not apply here, since this
  check reads the tool's own returned provenance rather than accumulated state.
- **Variants:**
  - Fabricated change-record fields left standing *(brainstorm)*

## Intervention Points

### Prevention
- Restrict change-tracker field values to verbatim/near-verbatim proposal content or the
  permitted literals `"not provided in proposal"` / `"unknown"` / `"see incident <id>"`.

### Detection
- Evaluate the **returned result** of `create_change_request`; treat non-empty
  `fabricated_fields` as a violation. This must be a post-call check — the arguments
  alone cannot reveal fabrication.

### Mitigation
- Deny with the offending field names attached so the agent corrects and resubmits
  rather than proceeding.
- Block downstream submissions while a change request stands with fabricated fields.

### Recovery
- Persist `field_provenance` with the record so responders and auditors can distinguish
  sourced fields from unsourced ones after the fact.

---

## Management Plan

[Not yet developed. Run failure management to develop a plan for this failure mode.]
