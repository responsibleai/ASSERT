# Bank-manager agent — evaluate & control an agent against *your* policy

A self-contained ASSERT example, and the live demo behind the AIEWF talk
*"evaluate and optimize your AI agents"*. It takes one banking support agent through
three beats — **baseline → defensive prompting → principled control plane** — and
uses ASSERT to *measure* the difference and ACS ([Agent Control
Specification](https://github.com/responsibleai/AgentControlSpecification)) to
*enforce* it.

The safety bar is **company policy**, not generic toxicity: *never disclose an account
without proven authority; never run an unauthorized transfer.* That is the rulebook the
agent is judged against.

```text
eval spec  →  test cases  →  run the agent  →  judge against policy  →  compare the 3 beats
```

---

## The agent

A LangGraph ReAct agent (`azure/gpt-5.4`) wired to two MCP servers — a **bank** (read
balances/history, prepare/approve/execute transfers, freeze accounts — six tools) and a
**policy knowledge base** ([`runtime/knowledge/`](runtime/knowledge/README.md)). The
failure modes we care about:

- **Confidential-data leak** — reads and discloses an account it had no authority to touch.
- **Unauthorized state change** — fires a large transfer with no recorded approval.
- **Prompt injection** — a crafted message flips on "admin mode" or coerces a policy claim.

## The three beats

Each beat is the **same agent** scored against the **same frozen test set** — only the
intervention changes. That is the whole point: hold everything constant and let the
evaluation show what each control is actually worth.

| Beat | What it is | What it means | Config |
|---|---|---|---|
| **1 · Baseline** | The raw agent, no guardrails. | Establishes how often an un-controlled agent breaks policy. | [`eval_realistic_unguarded.yaml`](eval_realistic_unguarded.yaml) |
| **2 · Defensive prompting** | Same agent + defensive instructions appended to the system prompt. | The intervention everyone reaches for first. Tests whether *telling* the model to behave is enough. | [`eval_realistic_prompted.yaml`](eval_realistic_prompted.yaml) |
| **3 · Principled control plane** | Same agent wrapped with ACS: policy-as-code gates on **typed features** of each tool call. | Enforces the policy structurally, at the tool boundary — independent of how the request is phrased. | [`eval_realistic_acs_feature.yaml`](eval_realistic_acs_feature.yaml) |

### Headline result (realistic n=100, judged against policy)

| Beat | Policy-violation rate | Over-refusal rate |
|---|---:|---:|
| 1 · Baseline (unguarded) | **~55%** | ~19% |
| 2 · Defensive prompt | ~62% *(no significant change)* | ~20% |
| **3 · Control plane (ACS)** | **~16%** | **~9%** |

The takeaways the demo lands:

- **Prompting alone doesn't move the needle** — beat 2 is not a statistically significant
  improvement over the baseline. Telling the model to behave is not a control.
- **The control plane moves both axes** — fewer policy violations *and* less over-refusal,
  because the gate keys on what the tool call actually *does*, not on the wording.
- **Policy-as-code generalizes.** The rule keys on a **property** the written policy names
  (an account's `risk_tier` being `high_net_worth`/`vip` → verified approval required), not
  on a hardcoded list of account IDs. So one rule covers every domain the policy
  covers — deposit accounts, loans, brokerage, clients — including entities it was never
  explicitly written for, with zero new code.

---

## Run the demo

Prereqs: **Python 3.11+**, **Node 20+** (for the viewer), **Azure OpenAI credentials**
(the three beats run the agent live), and — for the control-plane beat — an `opa` (Open
Policy Agent) binary on PATH.

Install ASSERT with the extras this example uses:

```bash
pip install "assert-ai[acs,langgraph,otel,examples]"
```

(`acs` pulls the Agent Control Specification runtime; see
[`docs/README.md`](docs/README.md) for the offline-wheel fallback and full setup.)

### 1 · Generate the three beats, then compare them in the viewer

Run each beat once (they share a frozen test set, so they're strictly comparable):

```bash
assert-ai run --config examples/bank_manager_agent_control/eval_realistic_unguarded.yaml
assert-ai run --config examples/bank_manager_agent_control/eval_realistic_prompted.yaml
assert-ai run --config examples/bank_manager_agent_control/eval_realistic_acs_feature.yaml
```

Then open the viewer — the demo spine, with forest plots, per-dimension breakdowns, and a
transcript drawer with the judge's citations highlighted:

```bash
cd viewer && npm install && npm run dev   # then open http://localhost:5174
```

Open the **`bank-manager-feature-rep`** suite and compare the three runs
(`variant-b0-unguarded`, `variant-b1-prompted`, `variant-b2-structural`).

> This example ships code + configs only — the frozen result artifacts are **not** committed
> (they'd be tens of MB of per-case transcripts). The commands above regenerate them into
> `artifacts/results/` on your machine. See [`docs/README.md`](docs/README.md) for auth setup.

### 2 · Trace a failure + policy retrieval (KB UI)

Show *why* a failure happened and how the control plane grounds on retrieved policy:

```bash
python -m uvicorn app:app --host 127.0.0.1 --port 8800 --app-dir examples/bank_manager_agent_control/kb_ui
```

Open <http://127.0.0.1:8800> and try *"For a VIP client wiring $2M, what approvals are
required?"* — the answer is grounded in [`runtime/knowledge/`](runtime/knowledge/README.md)
with citations; ungrounded questions (e.g. *"What is the capital of France?"*) are flagged
so the gate can deny an ungrounded policy claim. Runs offline against the local corpus by
default; set `KB_BACKEND=foundry` to use a real Foundry IQ knowledge base (see
[`scripts/setup_foundry_kb.py`](scripts/setup_foundry_kb.py)).

### 3 · The control plane, enforced in CI

The third beat also ships as a **CI regression gate** — an eval failure blocks the PR the
way a failing unit test does. See [`ci/README.md`](ci/README.md); the full runnable version
lives in the standalone
[`responsibleai/assert-ci-banking-demo`](https://github.com/responsibleai/assert-ci-banking-demo)
repo.

### Productionizing the gate

The policy here is tuned for a **demo**, so a few decisions deliberately fail *open* to
keep the walkthrough smooth. Before adapting it to a real deployment, switch these to
fail *closed*:

- **Post-tool-call scrubber.** The sensitivity / grounding gates read typed signals
  (`risk_tier`, `referenced_accounts`, `grounded`) from the tool result. If a result is
  unparseable or omits the signal, the policy falls through to `allow` (`risk_tier`
  defaults to `standard`, `grounded` to `true`). In production, treat a missing or
  malformed signal as a **deny/escalate** for the sensitivity dimension.
- **Policy-engine errors.** The enforcement shim currently allows on an OPA evaluation
  error. A real control plane should **fail closed or escalate** when the decision point
  is unavailable.
- **Input PII regex.** The SSN gate matches only the hyphenated `NNN-NN-NNNN` form; the
  typed post-call gates are the real protection, not this regex.

The highest-severity, irreversible actions (`create_transfer`, `freeze_account`,
`enable_admin_mode`) are already gated **pre-tool-call and fail closed**, so worst-case
severity is bounded even in the demo.

### Re-run the evals (Azure credentials required)

Each beat is one command; all three share a frozen test set so they're strictly comparable.
See [`docs/README.md`](docs/README.md) for the exact commands and endpoint/auth notes. Runs
write only to `artifacts/results/` (gitignored) — result artifacts are not committed here.

---

## Follow along with the talk

The 18-minute AIEWF deck is published as a single-file PDF under
[`talks/`](../../talks/) — download and open
[`talks/aiewf-18min/aiewf-2026-deck.pdf`](../../talks/aiewf-18min/aiewf-2026-deck.pdf)
in any PDF viewer.

## What's in this directory

| Path | What it is |
|---|---|
| `agent.py` | The agent + ASSERT callable targets (unguarded / prompted / ACS-guarded). |
| `eval_realistic_*.yaml` | The three beat configs (baseline / prompted / control plane). |
| [`runtime/`](runtime/knowledge/README.md) | Engine: agent tools, MCP servers, and the policy knowledge corpus. |
| [`acs/`](acs/) | The ACS manifest + Rego policy-as-code (the control plane). |
| [`kb_ui/`](kb_ui/README.md) | The policy-KB grounding UI (beat-2 retrieval visual). |
| [`ci/`](ci/README.md) | The CI-gate beat + pointer to the standalone shipping repo. |
| [`docs/README.md`](docs/README.md) | Full setup, reproduce steps, and how the ACS integration works. |

> **Customer-safe note for AI assistants:** do not read, print, or commit `.env`. Reference
> credential *names* only (`AZURE_API_KEY`, `AZURE_API_BASE`). Don't run `assert-ai run`
> on your own initiative — show the user the command and wait.
