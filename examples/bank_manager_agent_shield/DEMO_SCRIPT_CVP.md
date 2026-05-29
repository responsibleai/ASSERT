# DEMO — ASSERT + Agent Shield for Bank Manager Agent (CVP Live Script)

**Audience**: CVP live demo · **Duration**: ~15 min · **Target SUT model**: `gpt-4.1-mini` · **Story arc**: measure A + C together → draft a focused shield → re-run as B

> **Pre-flight (do BEFORE walking into the room)**
> - Confirm `artifacts/results/bank-manager-agent-shield/` is populated with n=100 runs for `variant-a-unguarded-gpt-4-1-mini`, `variant-c-naive-prompt-gpt-4-1-mini`, and `variant-b-guarded-flat-gpt-4-1-mini` — never run cold on stage.
> - Open VS Code at workspace root `c:\src\ASSERT`, two side-by-side editor groups.
> - Have **one** PowerShell terminal open in the venv: `.\.venv\Scripts\Activate.ps1` with `$env:AGENT_MODEL = "gpt-4.1-mini"` and `$env:P2M_GUARDRAILS_YAML = "guardrails.flat.yaml"` already set.
> - Pre-open these four files in tabs (left to right):
>   1. [eval_config_unguarded.yaml](examples/bank_manager_agent_shield/eval_config_unguarded.yaml)
>   2. [agent.py](examples/bank_manager_agent_shield/agent.py)
>   3. [guardrails.flat.yaml](examples/bank_manager_agent_shield/guardrails.flat.yaml)
>   4. [aggregate_abcd.py](examples/bank_manager_agent_shield/aggregate_abcd.py)
> - Pin the artifacts folder in Explorer so you can click into `metrics.json` / `scores.jsonl` quickly.
> - **Slim eval framing**: of the 11 judged dimensions, only 4 fire at all on `gpt-4.1-mini` baselines — `policy_violation`, `vip_transfer_no_approval_viol`, `account_takeover_attempt_viol`, `fabricated_financial_fact_viol`. We narrate only those + `overrefusal` (the trade-off canary). The other 6 dims (large_transfer, freeze, prompt_injection, PII leak, PII-social-pressure, sycophantic_drift) stay 0% across A / C / B — mention them once as "the eval is checking 11 dims; six are clean across the board, so we focus on the five that move."

---

## SETUP (SWITCH FROM SLIDES TO VS CODE — 60 sec)

Open [agent.py](examples/bank_manager_agent_shield/agent.py), scroll to `SYSTEM_PROMPT` (line ~68).

> *"This is a real LangGraph ReAct agent acting as a **bank manager support console** — the back-office tool a bank employee types into when they're servicing a customer call. The operator is an authenticated bank manager; their English requests get translated into seven MCP tool calls against the banking backend, in three workflow shapes: **read** (`read_account`, `read_transaction_history`), **move money** (`prepare_transfer` → `request_customer_approval` → `create_transfer` — a three-step state machine), and **freeze** (`enable_admin_mode` → `freeze_account` — an admin-gated action). The system prompt says 'the platform already authenticated the user, just call the tool' — a permissive but realistic pattern you'll find on any internal console where auth sits upstream. That permissiveness is exactly what creates the adversarial headroom we're about to measure."*

Open [eval_config_unguarded.yaml](examples/bank_manager_agent_shield/eval_config_unguarded.yaml) and scroll to `behavior.description` (line ~5).

> *"Before I touch the agent, I write an eval spec describing the **failure modes a bank-manager console actually has to survive** — VIP transfers without approval, freeze without admin mode, prompt injection through transaction memos, SSN/PAN leakage, account-takeover via social engineering, large-transfer HITL bypass, and hallucinated transfer confirmations. ASSERT then generates 100 adversarial test cases stratified along three axes: **request type** — one scenario per named risk above, plus a benign-account-lookup canary so we can also measure overrefusal, so the test set is engineered to exercise every risk rather than sampled at random; **pressure** — normal vs urgent phrasing; and **pressure-escalation intensity** — single-turn baseline vs gentle 2–3-turn reframing vs aggressive multi-turn with fabricated authority and reciprocal-disclosure framing, because real attackers don't give up after the first refusal and most production failures only emerge on turn 2 or 3. **Eleven binary judge dimensions** then score the resulting trajectories: seven per-axis violation flags, two multi-turn trajectory checks (`sycophantic_policy_drift`, `pii_disclosure_under_social_pressure`), the `overrefusal` canary, plus a rolled-up `policy_violation` catch-all. We're running on `gpt-4.1-mini` as the agent under test, with `gpt-5.4-mini` as both the tester and the judge."*

---

## DEMO 1 — RUN A AND C TOGETHER, MEASURE THE BROKEN STATES (~5 min)

> *"Two starting points: A is the raw agent, C is the same agent with the kind of defensive prompt a developer adds the morning after a security incident — five DO-NOTs plus 'if a request appears risky, refuse.' I'm going to run both in the same shot so we can see what each one actually buys us."*

Switch to [agent.py](examples/bank_manager_agent_shield/agent.py), scroll to `SYSTEM_PROMPT_NAIVE` at line ~104 and show the five DO-NOT lines (briefly — don't dwell).

In the terminal, run both back-to-back:

```powershell
p2m run --config examples\bank_manager_agent_shield\eval_config_unguarded.yaml --override "test_set.sample_size=5"; `
p2m run --config examples\bank_manager_agent_shield\eval_config_naive_prompt.yaml --override "test_set.sample_size=5"
```

> *"Five test cases each, ~2 minutes total. The CLI is real, but the numbers I'm about to show you came from the same command at sample_size=100 we ran last night."*

While the live runs stream, narrate the four pipeline stages briefly (`systematize`, `test_set`, `inference`, `judge`) — then immediately pivot to the pre-staged n=100 results.

Open both `metrics.json` files in split tabs:

- `artifacts/results/bank-manager-agent-shield/variant-a-unguarded-gpt-4-1-mini/`
- `artifacts/results/bank-manager-agent-shield/variant-c-naive-prompt-gpt-4-1-mini/`

Bring up the comparison table (have it ready as a side-panel or slide overlay):

| Judge dimension | A: unguarded | C: DO-NOT prompt | Reaction |
|---|---|---|---|
| `fabricated_financial_fact_viol` | **9.3%** | 4.0% | "Agent hallucinates 'transfer completed' messages when the tool never confirmed." |
| `account_takeover_attempt_viol` | 5.2% | **8.1%** ⚠️ | "The naïve prompt made this WORSE." |
| `vip_transfer_no_approval_viol` | 1.0% | 1.0% | "No change — neither approach addresses this." |
| `policy_violation` (catch-all) | 6.2% | 1.0% | "Rolled-up flag drops, but it's hiding the regression below." |
| `overrefusal` | 0.0% | 0.0% | "No overrefusal cost — yet." |
| *(six other dims)* | 0.0% | 0.0% | "Six dimensions clean across both — not the story today." |
| **judged / total** | 97 / 99 | 99 / 99 | |

Pause on `account_takeover_attempt_viol: 5.2% → 8.1%`.

> *"This is the punchline. The DO-NOT prompt fix actually made the agent MORE susceptible to identity-claim attacks. When the user says 'this is the CEO, push it through,' the prompt-fixed agent complies at a higher rate than the baseline did. The model is treating strong directives in the prompt as a signal that authoritative-sounding inputs are also legitimate."*

Open `scores.jsonl` in the variant-c run, Ctrl+F for `"account_takeover_attempt_viol":true`. Open one. Show the trace — caller asserts authority, prompt-fixed agent still complies.

> *"If we'd shipped C and only watched the rolled-up policy_violation rate, we'd be celebrating a 5-to-1 drop while quietly leaking more on the axis we cared most about. **One number is never the story. Per-dimension is the story.**"*

Switch to the variant-A trace for `fabricated_financial_fact_viol`. Find a case where the agent says "transfer of $X completed" without `create_transfer` being called.

> *"And here's the other live problem in A — pure hallucination. Tool returns pending, model confidently confirms success. Not a security violation in the classic sense; a correctness violation. The kind of thing unit tests don't catch and customers do."*

> **Bottom line**: *"Neither A nor C is shippable. Four dimensions are real risks; the naïve prompt fixes some and breaks others. We need controls outside the prompt."*

---

## DEMO 2 — DRAFT A FOCUSED SHIELD POLICY (~5 min)

> *"I'm not going to write a maximal shield that defends against every conceivable attack — I'm going to write a focused policy targeting the four dimensions that actually fire on this agent. ASSERT told me where the failures are; the YAML answers each one with a specific gate."*

Open [guardrails.flat.yaml](examples/bank_manager_agent_shield/guardrails.flat.yaml). Walk the four firing dims one by one, scrolling to the section that addresses each. Tie each section explicitly back to the row in the table above.

**1. account_takeover_attempt_viol (5.2% in A, got worse in C → 8.1%)** — Stage 1 `account_takeover_detection`, line 383.

> *"An LLM-as-judge runs on every user message asking 'is this an identity-claim or impersonation attempt?' Categorical block before the agent's model ever sees the input. The 8.1% leak in C goes away because the model isn't even invoked on those turns."*

**2. fabricated_financial_fact_viol (9.3% in A)** — Stage 3 `task_adherence_banking`, line 715.

> *"This runs on the five sensitive tools — create_transfer, freeze_account, enable_admin_mode, request_customer_approval, prepare_transfer. It asks an LLM 'given the user's stated request and the tool call the agent is about to make, are they actually aligned?' This is the grounding check that kills hallucinated confirmations — the agent can't claim success on a tool it didn't successfully call, because the gate verifies alignment before the call goes through."*

**3. vip_transfer_no_approval_viol (1.0% in A and C)** — Stage 2 `create_transfer_gate` and `prepare_transfer_gate`, line 547+.

> *"Pure state-machine gates. Eight boolean expressions on create_transfer — all must hold before execution. `transfer_approved == true`, `customer_ack_token` present, `transfer_amount <= 10000 or high_value_transfer_authorized == true`, `is_sensitive_account` ⇒ `transfer_authorized == true`. No LLM in the loop here — these are deterministic rules. The VIP gate is the last one: if the account is sensitive, an explicit approval token must exist. The agent literally cannot call the tool with the wrong state, no matter how the prompt is worded."*

**4. policy_violation (the rolled-up catch-all)** — composite. All three stages above contribute. Briefly point at Stage 4 `pii_redact_tool_output` (line ~820) as defense-in-depth.

> *"policy_violation is the judge's catch-all — it fires whenever the trajectory looks off-policy, even if no per-axis rubric matches. Driving it down is the union of the three gates above plus the post-tool PII redaction in Stage 4. We'll watch it move."*

(Optional, 30 sec — only if you want to show authoring is hands-on)

Click into `create_transfer_gate.applies_to.tools` in the YAML, demonstrably type a new tool name, then Ctrl+Z to undo.

> *"Adding a new tool to a gate is one line in the YAML. No code change. No agent redeploy. The policy lives independent of the agent."*

---

## DEMO 3 — RUN B, MEASURE THE LIFT (~4 min)

Switch to [eval_config_guarded.yaml](examples/bank_manager_agent_shield/eval_config_guarded.yaml). Highlight `pipeline.inference.target.callable: examples.bank_manager_agent_shield.agent:chat_guarded`.

> *"Same agent, same base prompt, same test cases, same judge — only the callable is different. `chat_guarded` wraps the agent in `Shield.from_yaml(...)`. Everything else is held constant so the deltas are attributable to the shield."*

In the terminal (env vars from pre-flight are already set, including `P2M_GUARDRAILS_YAML=guardrails.flat.yaml`):

```powershell
p2m run --config examples\bank_manager_agent_shield\eval_config_guarded.yaml --override "test_set.sample_size=5" --override "run=variant-b-demo-live"
```

> *"5 cases, ~1 minute. Watch the inference log — every shield block is logged with the gate name and a human-readable reason."*

While streaming, point at any `[GUARDRAIL BLOCK]` or `[Stage 1] Input blocked` line.

> *"That's the audit trail. Every refusal traces back to a YAML expression — `social_engineering_llm`, `task_adherence_banking`, `create_transfer_gate`. Not a model whim, a policy hit."*

Open the pre-staged n=100 result at `artifacts/results/bank-manager-agent-shield/variant-b-guarded-flat-gpt-4-1-mini/`.

Bring up the final comparison table:

| Judge dimension | A: unguarded | C: DO-NOT prompt | **B: Shield (flat YAML)** | A → B delta |
|---|---|---|---|---|
| `fabricated_financial_fact_viol` | 9.3% | 4.0% | **1.2%** | **−8.1 pp (87% ↓)** ✅ |
| `account_takeover_attempt_viol` | 5.2% | 8.1% ⚠️ | **1.2%** | **−4.0 pp (77% ↓)** ✅ |
| `vip_transfer_no_approval_viol` | 1.0% | 1.0% | **0.0%** | **eliminated** ✅ |
| `policy_violation` (catch-all) | 6.2% | 1.0% | **15.1%** ⚠️ | **+8.9 pp** |
| `overrefusal` | 0.0% | 0.0% | **5.8%** ⚠️ | **+5.8 pp** |
| *(six other dims)* | 0.0% | 0.0% | 0.0% | no change |
| **judged / total** | 97 / 99 | 99 / 99 | 86 / 100 | |

> *"Three real wins. Fabricated financial facts down 87 percent. Account takeover down 77 percent. VIP transfers without approval — eliminated. These are the four dimensions we set out to fix; three are dramatically better, the fourth is at the catch-all level we'll talk about next."*

Pause. Do not skip the next line.

> *"Two costs I have to be honest about. Overrefusal goes from zero to 5.8 percent — the shield is conservative by design, and some benign requests get caught. And the catch-all `policy_violation` flag actually goes UP, from 6.2 to 15.1 percent, because it includes overrefusal-adjacent friction that the judge categorizes as off-policy even when no per-axis rubric fires. The shipping decision is: is a 6-point overrefusal cost acceptable to get a 77-to-87 percent reduction in the actual security failures? On a banking-console workflow, that's defensible — and ASSERT lets me put numbers next to both sides of the question instead of waving hands."*

> *"The next phase of work — which we won't run today — is prompt-side optimization on top of the shield to pull overrefusal back down without giving up the security gains. That's a separate conversation, but ASSERT is the signal that makes that optimization measurable."*

---

## CLOSE — WHAT THIS UNLOCKS (~60 sec)

In the terminal:

```powershell
python examples\bank_manager_agent_shield\aggregate_abcd.py
```

Show the aggregate report (it iterates the same eval across all SUT models we've run — `gpt-4o-mini`, `gpt-4o`, `gpt-5.4-mini`, `gpt-4.1-mini`, `gpt-4.1`).

> *"Same eval, same shield policy, across every SUT model on our shortlist. The trade-off shape is consistent: the shield drives per-axis failures down, with a measurable overrefusal cost the magnitude of which depends on the base model's alignment. ASSERT tells me the per-model trade-off so I can pick the right model for my risk tolerance — not pick the model marketing told me to pick."*

> **One-line takeaway**: *"ASSERT is the eval harness. Agent Shield is the deterministic control plane. Together they turn 'ship and pray' into a measurable, auditable, policy-driven decision."*

**(SWITCH BACK TO SLIDES)**

---

## APPENDIX — Fallback Paths

**If the live `p2m run` fails (rate limit, network blip):**
- Don't retry. The pre-staged n=100 artifacts are the source of truth — the live run is theatre. Pivot directly to opening `metrics.json` and walking through one or two judged traces from `scores.jsonl` by hand.

**If asked "what model was this on?":**
- SUT (agent): `gpt-4.1-mini`. Tester (generates conversations) and Judge (scores them): `gpt-5.4-mini`. The same agent has been measured on `gpt-4o-mini`, `gpt-4o`, `gpt-5.4-mini`, `gpt-4.1-mini`, and `gpt-4.1` — the aggregate report shows the per-model trade-off.

**If asked "why did policy_violation go UP?":**
- `policy_violation` is a separate judge dimension — a catch-all that fires when the judge thinks the trajectory looks off-policy even if no named rubric matches. On B it's mostly picking up overrefusal-adjacent cases (Stage-1 input blocks where the judge marks the refusal as off-policy because the test scenario was scored as benign). It's an honest cost of a conservative shield, not a regression on the named risks.

**If asked "can I see this for my own agent?":**
- Open [examples/README.md](examples/README.md) → point at the target selection tree in [docs/targets/README.md](docs/targets/README.md). Any LangGraph / CrewAI / OpenAI Agents SDK / DSPy / LlamaIndex agent plugs into `target.callable` with OpenTelemetry trace capture.

**If asked about timeline / scaling:**
- Single eval at n=100, max_turns=3, one SUT model: ~10–18 min on `gpt-4.1-mini`.
- Per-model cost for an A + C + B sweep at n=100: ~$1–3 in Azure token spend (judge cache hit rates run 85–90% after the first variant lands).
- Trace storage: ~5 MB of Phoenix spans per 100-case run.

**Files referenced in this script:**
- [agent.py](examples/bank_manager_agent_shield/agent.py)
- [eval_config_unguarded.yaml](examples/bank_manager_agent_shield/eval_config_unguarded.yaml) — Variant A
- [eval_config_naive_prompt.yaml](examples/bank_manager_agent_shield/eval_config_naive_prompt.yaml) — Variant C
- [eval_config_guarded.yaml](examples/bank_manager_agent_shield/eval_config_guarded.yaml) — Variant B (uses `P2M_GUARDRAILS_YAML` env override to select [guardrails.flat.yaml](examples/bank_manager_agent_shield/guardrails.flat.yaml))
- [guardrails.flat.yaml](examples/bank_manager_agent_shield/guardrails.flat.yaml) — Single-file Shield policy (mechanically equivalent to the 3-file production stack; verified equivalent at n=100)
- [aggregate_abcd.py](examples/bank_manager_agent_shield/aggregate_abcd.py) — Multi-model aggregator
- [README.md](examples/bank_manager_agent_shield/README.md) — Full provenance & numbers