# Problem

## What we're building

A SaaS customer-support chatbot for a **B2B billing product**. Authenticated
customers use it in-session to:

- check invoices,
- update payment methods,
- change subscription plans,
- request refunds (up to **$200** within policy).

## What it can do

- Look up account data / PII for the authenticated caller.
- Issue refunds within policy.
- Escalate to a human agent.

## Constraints it must honor

- **Refuse** legal, tax, and financial advice.
- **Must not expose another customer's data** (account, PII, invoices).
- **Must verify identity before high-risk actions** — plan changes,
  cancellations, and refunds (and, by extension, payment-method updates).

## Why it matters

The agent has real, consequential tools (mutations to billing state, access to
PII, money movement). A helpful-but-unguarded assistant can be socially
engineered into acting on an unverified session, leaking another customer's
data, exceeding the refund cap, or straying into regulated advice — each of
which is a compliance and trust failure for a B2B billing vendor.

## Success criteria

The agent should complete legitimate in-policy requests for the verified
account holder while refusing or escalating out-of-policy ones, with
measurable, low rates of: unverified high-risk actions, cross-customer data
exposure, over-cap refunds, and prohibited advice — without over-refusing
legitimate requests.
