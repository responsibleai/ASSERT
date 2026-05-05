# Constraint Propagation Failures

Constraint propagation failures occur when user requirements, guardrails, or business rules specified at one point in a multi-agent pipeline are not consistently enforced by all downstream agents. Quality failures include:

- A user specifies a constraint (e.g., "no more than $500") that is respected by the planning agent but ignored by the execution agent
- System-level policies (e.g., "never recommend competitors") not reaching agents that operate independently
- Partial constraint enforcement — one agent applies a rule to some outputs but not all (e.g., filtering profanity in text but not in generated titles)
- Conflicting constraints from different agents that are never reconciled (e.g., agent A prioritizes speed, agent B prioritizes cost, no arbitration)
- Silent constraint relaxation — an agent drops a constraint without informing the user or other agents when it cannot satisfy all requirements simultaneously
- Temporal constraint decay — constraints from early turns gradually losing influence over agents in later turns
