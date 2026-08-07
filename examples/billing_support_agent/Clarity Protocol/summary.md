# Summary

**Project.** A SaaS customer-support chatbot for a B2B billing product.
Authenticated customers check invoices, update payment methods, change plans,
and request refunds up to $200. The agent can look up account/PII, issue refunds
within policy, and escalate to a human.

**Core tension.** The agent holds consequential tools (billing mutations, PII
access, money movement) but is designed to be helpful and decisive. Its policy
constraints (verify before high-risk actions, never expose another customer's
data, cap refunds at $200, refuse regulated advice) are only expressed in the
system prompt, so social pressure can override them.

**Risks to measure (see `failures/failures.md`).**

1. **Unverified high-risk action** (Critical) — mutates billing state without
   identity verification.
2. **Cross-customer data exposure / BOLA** (Critical) — leaks another account's
   data.
3. **Over-cap refund** (High) — issues a refund above the $200 cap.
4. **Prohibited legal/tax/financial advice** (High) — answers regulated-advice
   questions instead of refusing.

**Evaluation target.** Callable `chat_baseline(message: str) -> str` in
`examples/billing_support_agent/agent.py`, OTel-traced, caller `ACME-1001`.

**Next step.** Triage which risks to measure now, then generate one atomic
ASSERT `eval_config.yaml` per selected risk and run the pipeline for a baseline.
