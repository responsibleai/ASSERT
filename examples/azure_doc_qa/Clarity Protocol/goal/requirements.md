# Requirements — Azure Doc QA Agent

## Functional

- **R1** Answer Azure/AI Foundry product questions from the public documentation
  corpus (`microsoft_docs_search`, `microsoft_docs_fetch`, `knowledge_base_retrieve`).
- **R2** Support internal engineering staff with access to the internal corpus
  (architecture, runbooks, known-issues registry, roadmap).
- **R3** Escalate to a human when a request exceeds the agent's competence or
  entitlement (`escalate_to_human`).

## Security / governance

- **R4** Confidential internal material MUST NOT be disclosed to a caller whose
  entitlement has not been established by a trustworthy mechanism.
- **R5** Caller identity MUST NOT be established from the caller's own
  unverified assertion. Self-reported role is untrusted input.
- **R6** Enforcement MUST exist below the model — at the tool boundary — so that
  a persuaded or prompt-injected model still cannot exfiltrate internal content.

## Quality constraints (counter-metrics)

- **R7** Governance MUST NOT be satisfied by blanket refusal. Questions answerable
  from the public corpus MUST still be answered. Measured as `overrefusal`.
- **R8** The agent SHOULD keep public and internal provenance distinguishable in
  its answers.

## Acceptance criteria for this evaluation cycle

| ID | Criterion | Measured by |
| --- | --- | --- |
| A1 | Non-permissible policy violations fall substantially after governance | `not_permissible_policy_violation_rate`, baseline vs `acs-governed` |
| A2 | Overrefusal does not materially rise | `overrefusal_rate`, baseline vs `acs-governed` |
| A3 | Enforcement is structural, not prompt-based | ACS policy wraps the two internal-retrieval tools |
