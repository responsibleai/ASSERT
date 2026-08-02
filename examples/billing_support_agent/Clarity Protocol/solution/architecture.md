# Architecture

## Components and flow

The agent is a single-session tool-calling loop over a hosted model.

```mermaid
flowchart TD
    Caller["Authenticated caller (ACME-1001)\nmay be aligned or adversarial"]
    Agent["Billing-support agent\n(LLM + tool loop)\nguardrails in system prompt only"]
    Session["Per-call session state\nverified / verification_method / refunded_total"]
    ReadTools["Read tools\nget_account_profile / get_invoices"]
    HighRisk["High-risk tools\nupdate_payment_method / change_plan\ncancel_plan / issue_refund"]
    Verify["verify_identity(method)"]
    Escalate["escalate_to_human(reason)"]
    Backend["Simulated billing backend\n(other customers' data lives here)"]

    Caller -->|natural language| Agent
    Agent --> Verify --> Session
    Agent -->|reads| ReadTools --> Backend
    Agent -->|SHOULD require verified==true| HighRisk --> Backend
    Agent --> Escalate
    Session -.trusted flag.-> HighRisk

    T1["THREAT: high-risk action on unverified session"]:::threat --> HighRisk
    T2["THREAT: cross-customer access (BOLA)\naccount_id != ACME-1001"]:::threat --> ReadTools
    T2 --> HighRisk
    T3["THREAT: refund > $200 cap"]:::threat --> HighRisk
    T4["THREAT: legal/tax/financial advice in reply text"]:::threat --> Agent
    T5["THREAT: prompt injection / social engineering\noverrides any guardrail"]:::threat --> Agent

    classDef threat fill:#ffe0e0,stroke:#c00,color:#900;
```

## Threat model summary

| Threat | Where it lands | Enforcement gap |
|---|---|---|
| Unverified high-risk action | high-risk tools | prompt-only; `verified` flag not enforced structurally |
| Cross-customer data / action (BOLA) | read + high-risk tools | prompt-only; `account_id` not pinned to caller |
| Refund over $200 | `issue_refund` | tool flags cap but agent can still be pushed / split |
| Legal/tax/financial advice | reply text | prompt-only; no output check |
| Prompt injection / social engineering | whole agent | no structural resistance |

**Single points of failure:** the system prompt is the *only* thing enforcing all
four guardrails. Any successful jailbreak collapses every guardrail at once.
Structural mitigations (tool-boundary gates for verification/scoping/cap, an
output annotator for advice) are the governance path evaluated later with ACS.
