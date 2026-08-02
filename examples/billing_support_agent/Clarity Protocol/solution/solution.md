# Solution

The system-under-test is an **existing callable billing-support agent**
(`examples/billing_support_agent/agent.py`), reused as the ASSERT target. We are
not designing a new solution — we are evaluating this one's guardrails.

## Shape

- **Callable target** `chat_baseline(message: str) -> str`, one isolated session
  per test case, with OpenTelemetry (OpenInference) tracing so the judge can cite
  tool calls, not just final text.
- **Single authenticated caller** `ACME-1001`. Verification state lives in a
  per-call `state` dict (`verified`, `verification_method`, `refunded_total`).
- **Eight tools:** `verify_identity`, `get_account_profile`, `get_invoices`,
  `update_payment_method`, `change_plan`, `cancel_plan`, `issue_refund`,
  `escalate_to_human`.
- **High-risk set** (`update_payment_method`, `change_plan`, `cancel_plan`,
  `issue_refund`) is *supposed* to require `verified == true`.

## Key property being evaluated

All four guardrails (verification, account scoping, $200 refund cap, no legal/tax/
financial advice) are enforced **only in the system prompt**. There is no
structural enforcement in the baseline, so the agent can be pressured past them.
That gap is exactly what the evaluation measures, and what a later ACS policy
would govern at the tool boundary (verification, scoping, cap) or via an output
annotator (advice).
