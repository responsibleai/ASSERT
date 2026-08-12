# Billing Support Agent

A worked ASSERT example for a **SaaS B2B billing customer-support chatbot**.

The agent serves authenticated customers who check invoices, update payment methods, change plans, and
request refunds up to $200. It can look up account/PII, issue refunds within policy, and escalate to a
human. It must refuse legal/tax/financial advice, must not expose another customer's data, and must
verify identity before high-risk actions (plan changes, cancellations, refunds).

## What's in this directory

| Path | What it is |
|---|---|
| `agent.py` | The agent itself. Exposes `chat_baseline`, the callable ASSERT evaluates. |
| `evals/<atomic_behavior>.yaml` | One ASSERT eval suite per behavior — behavior taxonomy, test-set generation, target, and judge. |
| `README.md` | This file. |

`__init__.py` makes the folder importable, so each config can resolve its
`examples.billing_support_agent.agent:chat_baseline` target.

## Tools

The agent is wrapped as an [ASSERT callable target](../../docs/targets/callable.md) so the judge can
inspect the tool trace, not just the final reply. Every tool is mocked in-process — no live billing
system is contacted.

| Tool | Purpose |
|---|---|
| `verify_identity` | In-session step-up challenge (otp, reauth, security questions). Policy requires it before any high-risk action. |
| `get_account_profile` | Retrieve an account profile (owner, plan, contact, payment method). |
| `get_invoices` | Retrieve invoices for an account. |
| `update_payment_method` | Update the payment method. **High-risk** — policy requires a verified session. |
| `change_plan` | Change the subscription plan. **High-risk** — policy requires a verified session. |
| `cancel_plan` | Cancel the subscription. **High-risk** — policy requires a verified session. |
| `issue_refund` | Issue a refund. **High-risk** — policy requires a verified session and caps refunds at $200. |
| `escalate_to_human` | Hand the conversation to a human support agent. |

### The controls are prompt-only, on purpose

The full billing policy — account scoping, the verification gate, the refund cap,
the advice boundary — is stated in the agent's system prompt. **No tool enforces
it.** Account-scoped results report `caller_account_id` and a
`requested_account_scope` of `caller` or `foreign`, and high-risk results report
`verified_session`, so the agent has everything it needs to apply the policy and
the judge can see whether it did. But the read tools serve any `account_id`, and
the high-risk tools execute on an unverified session.

That is deliberate: the baseline has been told the rules but can still be
argued out of them, which is what makes the measurement meaningful.

Foreign accounts in the directory hold distinct owners, contacts and invoices, so
a cross-account disclosure is a real disclosure rather than the caller's own
record under someone else's label.

## The two measured risks

| Risk | Failure mode |
|---|---|
| `unverified_high_risk_action.yaml` | Executes/commits a plan change, cancellation, refund, or payment-method update **before** current-session identity verification |
| `cross_customer_data_exposure.yaml` | Looks up or acts on **another customer's** account from a user-supplied ID |

Each risk gets its own suite under `evals/`, so the two are measured independently.

## Environment Variables

Set these in a `.env` at the repo root — never commit it.

| Variable | Purpose |
|---|---|
| `AZURE_API_KEY`, `AZURE_API_BASE` | Azure OpenAI credentials for the agent and the judge. Swap models in the files under `evals/` for any other [LiteLLM provider](https://docs.litellm.ai/docs/providers). |
| `AZURE_API_VERSION` | Optional. Defaults to `2024-08-01-preview`. |
| `BILLING_AGENT_MODEL` | Optional. Overrides the agent model (default `azure/gpt-5.4-mini`). |
| `PHOENIX_PROJECT_NAME` | Optional. Trace project name (default `billing-support-agent`). |

## How to run

```powershell
# 0. install
python -m pip install -e ".[otel]"
Copy-Item .env.example .env
# Set AZURE_API_KEY and AZURE_API_BASE.

# 1. run each eval suite
assert-ai run --config examples/billing_support_agent/evals/unverified_high_risk_action.yaml --concurrency 6 --output json
assert-ai run --config examples/billing_support_agent/evals/cross_customer_data_exposure.yaml --concurrency 6 --output json

# 2. inspect the results
assert-ai results status billing-unverified-high-risk-action baseline --json
assert-ai results status billing-cross-customer-data-exposure baseline --json
```

> On Windows PowerShell, set `$env:PYTHONUTF8="1"; $env:PYTHONIOENCODING="utf-8"` before
> `results status --json` (the console otherwise crashes on a Unicode arrow).

## What you should see

Artifacts land under `artifacts/results/<suite>/` — for example
`artifacts/results/billing-unverified-high-risk-action/`. Suite-level files sit at the top; the
per-run files sit under `baseline/`.

| File | What it holds |
|---|---|
| `taxonomy.json` | The generated behaviour categories. |
| `test_set.jsonl` | The generated test cases — single-turn prompts and multi-turn scenarios. |
| `baseline/inference_set.jsonl` | Full transcripts, including every tool call and its result. |
| `baseline/scores.jsonl` | Per-test-case judge verdicts with justifications. |
| `baseline/metrics.json` | Aggregate behaviour violation rates. |
| `baseline/config.yaml` | A snapshot of the exact config that produced the run. |

A useful failure analysis reads one case end to end: what the caller asked (`test_set.jsonl`), which
tools the agent called and in what order (`inference_set.jsonl`), and whether the judge counted it as
a violation (`scores.jsonl`).

## Notes

- All billing tools are mocked in-process, so running the eval never touches a real billing system.
- `artifacts/` is gitignored — runs stay local and are never committed.
- Each risk is its own suite, so you can rerun one without regenerating the other's test set.
