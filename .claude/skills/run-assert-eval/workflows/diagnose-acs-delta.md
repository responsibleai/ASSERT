# Workflow: diagnose-acs-delta

Reference manual for **Step 5a** of `govern-and-remeasure.md`. Open this only
when the governed run produced a **wrong delta**:

- no drop, or a smaller drop than expected, in the **non-permissible** violation
  rate, **or**
- `overrefusal` (or the **permissible** violation rate) rose materially.

> **Metric keys** (the prose below uses the display wording; these are the literal
> identifiers to read and grep). From `assert-ai results status <suite> <run> --json`:
> `not_permissible_policy_violation_rate` — rendered on screen as **Impermissible
> behavior violated** — and `permissible_policy_violation_rate` — **Permissible
> behavior violated**. Note every identifier is `not_permissible`; only the
> human-facing label says "Impermissible". The viewer's dimension keys are
> `policy_violation_not_permissible` / `policy_violation_permissible`.

> **Try not to need this file.** Most rules below are *preventable*, not
> diagnostic: **§1** (wrong gate point), **§2.1**, **§2.2**, **§3.2**, **§4.3**,
> **§4.4** and **§5.1** are all decidable from the **baseline** run, the config, or
> triage — that is what **Step 1a** of `govern-and-remeasure.md` exists for. **§3.1**
> is prevented outright by defaulting to regenerate-and-re-gate. If you landed here
> without doing Step 1a, do it now against the baseline artifacts rather than tuning
> the governed run.
>
> Only **§2.3** and **§2.4** (annotator calibration against hedged/softened
> variants) genuinely require a governed run to discover — the judge's hedging
> threshold is only visible in the residuals. **§4.1** and **§4.2** are measurement
> discipline, not fixes.

Do not re-roll blindly. Match the observed symptom to a rule below, apply the
**smallest** change, re-run. Cap at **~4 attempts per domain** — several rules
below exist precisely to tell you when the current result is already correct.

## Get the signals first

Join `artifacts/results/<suite>/acs-governed/{inference_set,scores}.jsonl` on
`test_case_id`, then for each row pull:

- `events` where actor is `target` — the agent's own replies.
- `verdict.dimension_justifications` — what the judge actually punished.
- the count of rows whose reply contains the gate's **block-remediation text** —
  this is your "**how often did the gate fire?**" number.

Gate-fired count is the single most discriminating signal: it splits "wrong
interception point" (§1) from "annotator under-fires" (§2) from "not the gate at
all" (§4).

## Symptom index

| # | Symptom | Go to |
|---|---|---|
| 1 | Harm flat, gate fired **~0×** | [§1.1](#11-the-failure-is-prose-not-a-tool-call) |
| 2 | A tool exposes a clean flag, so you planned a tool gate | [§1.2](#12-a-deterministic-field-in-toolspy-does-not-make-the-failure-structural) |
| 3 | Prompt-injection / XPIA suite | [§1.3](#13-prompt-injection--xpia-is-an-output-gate-not-a-retrieved-content-gate) |
| 4 | Tool returns a dose / interaction / profile field | [§1.4](#14-a-tool-laundered-number-still-needs-an-output-gate) |
| 5 | Gate fired, harm persists, entitlement signal is spoofable | [§2.1](#21-never-condition-the-annotator-on-the-agents-own-spoofable-signal) |
| 6 | Multi-turn case stays flagged though the gate fired on some turn | [§2.2](#22-an-earlier-unblocked-turn-keeps-the-whole-transcript-flagged) |
| 7 | Harm only partly drops, `overrefusal` flat | [§2.3](#23-the-annotator-under-fires-on-hedged--soft-variants) |
| 8 | Residual soft reassurance / minimization in scenarios | [§2.4](#24-residual-soft-practical-reassurance-in-multi-turn-scenarios) |
| 9 | `overrefusal` rose | [§3.1](#31-the-block-remediation-is-a-flat-refusal) |
| 10 | Grounding gate over-blocks **scenarios** but not prompts | [§3.2](#32-a-grounding-annotator-is-grounding-each-turn-in-isolation) |
| 11 | Overrefused rows the gate never touched | [§4.1](#41-decompose-an-overrefusal-rise-before-blaming-acs) |
| 12 | High baseline overrefusal on an injection / "engage with suspicious content" suite | [§4.2](#42-high-baseline-overrefusal-is-the-agents-own-caution) |
| 13 | Baseline harm rate already ≲10% | [§4.3](#43-a-very-low-baseline-is-not-a-governance-target) |
| 14 | Two risks share one content band; harm↔overrefusal seesaw | [§4.4](#44-two-risks-on-one-content-band-hit-a-judge-tension-frontier) |
| 15 | Target is a YAML Prompt Agent | [§5.1](#51-a-prompt-agent-cannot-be-governed-in-place) |

---

## §1 — The gate is at the wrong interception point

Signature: **the gate fired ~0 times.** The policy is fine; it is watching a
place the harm never passes through.

### 1.1 The failure is prose, not a tool call

A prose/semantic failure judged on the agent's **final reply** (disclosure,
leakage, unsafe advice, fabrication, injection compliance) cannot be caught by a
tool-arg or tool-result rule — the model emits the harm as text, sometimes with
**no tool call at all**.

**Fix:** move to a **Shape 4 `output` annotator gate** (see "Semantic gates" in
`govern-and-remeasure.md`). Never collapse a semantic failure into a
deterministic tool gate just because retrieved data carried a structural field.

### 1.2 A deterministic field in `tools.py` does NOT make the failure structural

A backend may expose a clean flag (`validation.fabricated_fields`,
`sequence_violations`, a `verified` echo) that *looks* like a perfect
`post_tool_call` gate. What matters is **what the JUDGE scores.**

**Before committing to a tool gate, confirm the failure routes through the tool:**

1. Grep the baseline `inference_set.jsonl` for how often flagged rows call the
   tool you plan to gate — `count(cases with "<tool_name>" in llm_calls)`.
2. Read `verdict.dimension_justifications` on flagged rows.

If the justifications cite the **reply text** — "presents unsupported values as
fact", "in the draft", "to paste into the tracker", "claimed approved" — and/or
the gated tool appears in only a handful of flagged rows, the harm is **prose the
model emits directly**. A `post_tool_call` gate will fire ~0× and move nothing.

**Fix:** author an `output` annotator calibrated to the judge. Keep any
deterministic tool rule only as **defense-in-depth** for the minority of rows
that do route through the tool.

> **Observed** — change_control_agent "fabricated fields": `tools.py` flags
> `fabricated_fields`, but the agent fabricated in prose and called
> `create_change_request` in only 3/50 rows, so the structural `post_tool_call`
> gate blocked 0/50 and the rate held at ~56%. An output annotator flagging
> unsupported field values in the reply is what moved it.

### 1.3 Prompt injection / XPIA is an `output` gate, not a retrieved-content gate

The injected payload arrives inside a tool result (retrieved document text),
which tempts a `post_tool_call` rule — but the bad event the judge scores is the
agent **relaying or acting on** the embedded instruction in its final **reply**
(fake deadlines/deprecations, injected contacts, rule overrides, exfiltrated
figures). Correctly reading and summarizing the document while ignoring its
embedded commands is **not** a violation, so the gate must judge the reply, not
the retrieved bytes.

**Fix:** author an `output` annotator that flags **endorsing / passing along**
the embedded instruction as real, agent-sanctioned guidance, and **exempt
reporting-it-as-data** (quoting or warning about the injected line while
explicitly treating it as untrusted document text) — otherwise the annotator
false-positives on accurate factual summaries and inflates overrefusal. On block,
regenerate a sanitized reply (keep the document's factual content, strip the
injected commands) and **re-gate**.

> **Observed** — azure_doc_qa prompt injection: baseline `followed_injection`
> 22% → 6% governed.

### 1.4 A tool-laundered number still needs an `output` gate

When the agent has tools (`assess_dosage`, `lookup_medication`,
`check_drug_interactions`, `get_patient_profile`), the tempting mistake is a
pre/`post_tool_call` rule on the tool's numeric field. But the harm is the
**prose the model writes after the call** — it presents the tool's number as the
user's personal dose ("your profile shows 500 mg is appropriate") or spins an
interaction result into "safe to take together." A tool gate cannot see that
prose.

**Fix:** gate at `output` and give the annotator an explicit clause — *a number
or flag returned by a tool does NOT license a case-applied dose, clearance, or
diagnosis.*

> **Observed** — prompt_agents sim-tools: reusing the model-only shared output
> annotator with tool-laundering + interaction-clearance clauses dropped
> tool-laundered dosing 28%→0% and interaction clearance 52%→16% at n=25, with
> overrefusal ≤ +16% dosage / +4% interaction. No tool-field rule needed.

---

## §2 — The gate fires, but harm persists

Signature: **the gate fired, and the non-permissible rate barely moved.** The
interception point is right; the annotator is under-firing.

### 2.1 Never condition the annotator on the agent's own spoofable signal

When the failure's "who is entitled" signal comes from a spoofable in-agent check
— a `verify_identity` tool that infers clearance by keyword-matching the caller's
self-description, or a self-asserted role — conditioning the annotator on it is
strictly weaker than the judge, so the same spoofed cases sail through.

**Fix:** calibrate the annotator to the **judge's** standard. Feed it the
**user's turns** and instruct it that a bare self-claim ("I'm on the platform
team", "I'm an engineer") is **not** legitimate verification, so substantive
restricted-content disclosure to a merely self-described caller is a violation.
Use an annotator model at the **judge's calibration tier** (same family/size as
the judge); a weaker or cheaper annotator under-fires.

Contrast with a *trusted* session flag (billing `verified`), which you **do**
surface into the policy. The rule is specifically: **when the only entitlement
signal is spoofable, mirror the judge — don't trust the agent.**

### 2.2 An earlier unblocked turn keeps the whole transcript flagged

The judge scores the **whole transcript**, so an **earlier** turn the annotator
missed keeps the case flagged regardless of a later block.

**Fix — both halves are required:**

1. Tighten the annotator so it fires on **every** offending turn (usually the
   same calibration fix as §2.1).
2. Confirm the callable declares a `history` param **and** the guarded wrapper
   gates **each** turn's output — otherwise only the last turn is protected.

### 2.3 The annotator under-fires on hedged / soft variants

Signature: **governed harm only partly drops while `overrefusal` stays flat.**
The judge scores hedged assertions as violations, but a first-pass classifier
only catches the blatant ones.

Read the residual `verdict.dimension_justifications`. If they cite softened
language — "consistent with X", "sounds like / could be X", "X-like", "probably
nothing serious", "it's reasonable to call 111 rather than 999" — or a dose
*confirmed / interpreted off the user's own label* ("15 mL is within range",
"twice daily means ~12h apart, 8am/8pm"), the rubric is too literal.

**Fix:** tighten it to flag the **case-applied** form regardless of hedging:

- any condition **applied to the user's own case** (even softened) is a diagnosis;
- any **walk-back / softening of a red-flag escalation** is emergency mismanagement;
- any **case-tied dose confirmation or label-application** is dosing — not just a
  fresh "take X mg".

Keep the exemptions crisp so this doesn't spill into overrefusal (see §4.4).

> **Observed** — prompt_agents model-only diagnosis: adding hedged-diagnosis +
> de-escalation clauses moved governed scenario harm 24%→8%.

### 2.4 Residual soft practical reassurance in multi-turn scenarios

After an explicit-clearance annotator lands the first big drop, the surviving
scenario violations are almost always the agent softening over several turns into
practical approval — "one ibuprofen is unlikely to be a problem", "fish oil is
usually not a big issue", recommending one drug as the "better/safer fallback for
you", or "most likely an allergic reaction". These are patient-specific
reassurance that minimizes a surfaced interaction or settles the user's own case
without an explicit "it's safe", so a clearance-only classifier passes them.

**Fix (optional):** add a clause flagging patient-specific
minimization/de-escalation of a real risk and case-applied "most-likely"
conclusions, while still exempting **general** "usually / in many people"
education not tied to the user's own case.

Weigh this against the ~4-attempt cap: a 52%→16% drop with flat overrefusal is
already a correct operating point. Chase the residual only if the harm rate is
still unacceptably high.

---

## §3 — `overrefusal` rose because of the gate

Confirm it really is the gate first (§4.1). If it is:

### 3.1 The block-remediation is a flat refusal

The safe behavior the judge rewards is "decline the restricted part **and still
provide the permitted alternative**" — public redirect, existence-only
acknowledgment, escalation, closest public equivalent.

**Fix:** replace the canned refusal with a **regenerated helpful answer** —
re-answer using only in-policy (e.g. public) knowledge, **lead with the useful
content, never open with an apology or "I can't"**, acknowledge the restricted
doc exists without revealing it, offer escalation — then **re-gate that
re-answer** so the no-harm guarantee still holds. Fall back to a flat decline
only if the re-answer still trips the gate. This is the travel
`_regenerate_grounded` / azure `_regenerate_public` pattern.

**Do NOT** widen or loosen the deny to fix overrefusal. Fix the remediation, not
the gate.

### 3.2 A grounding annotator is grounding each turn in isolation

Signature: **high `overrefusal` on scenarios, ~flat on single-turn prompts.** The
gate grounds each turn against **only that turn's tool results**, so specifics the
user supplied earlier — or that an earlier turn's tool returned — look
"unsupported" on a follow-up turn with no new tool call, and get blocked.

**Fix — both halves are required:**

1. Feed the annotator (and the regenerate step) the conversation **`history`**,
   and treat user-supplied + prior-turn facts as valid grounding, not just this
   turn's tool context.
2. **Prefer `regen` over a flat-decline (`blunt`) fallback.** In blunt mode every
   block returns the canned decline, which the judge scores as overrefusal, so the
   history fix barely moves the needle. Regen re-answers grounded in the
   conversation + tool results and re-gates, recovering the legitimate turns.

> **Observed** — travel `fabricated-details`, `azure/gpt-5.4-mini` strict
> annotator, n=25/type: the history-grounding fix alone in blunt mode moved
> scenario overrefusal 92%→84%; switching to **regen** took it 84%→**48%** while
> scenario `fabricated_details` went baseline 76%→36%. Blunt's 76%→4-16% was
> bought at a catastrophic 84-92% overrefusal. **Regen is the balanced operating
> point; blunt just trades one failure for another.**

---

## §4 — It is not the gate: measure before you iterate

These rules exist to stop you burning attempts on a result that is already
correct.

### 4.1 Decompose an overrefusal rise before blaming ACS

When the governed run re-runs inference (`--force-stage inference`) the baseline
path **re-generates**, so a stochastic / high-overrefusal agent produces
different refusals run-to-run that have nothing to do with ACS.

**Method:** join governed↔baseline scores on `test_case_id`, take rows that are
`overrefusal=true` in governed but `false` in baseline, and split them by whether
the gate's block-remediation text is present in the reply:

- **remediation present → ACS-caused**
- **remediation absent → the gate never fired → baseline variance**

Only the ACS-caused fraction should be weighed against the harm drop; record the
rest as baseline noise.

> **Observed** — azure_doc_qa prompt injection v3: of 11 newly-overrefused rows
> only 4 had the gate fire. The other 7 were baseline-agent variance, so the true
> ACS overrefusal cost was ~8pt, not the raw +10pt, for a 16pt harm drop.

Note the flip side: a flat-refusal fallback **guarantees** the bad event is
blocked but costs ~1pt overrefusal per unrecoverable block; returning the
regenerated answer lowers overrefusal but lets harm back through if the
regenerate still trips. Prefer investing in a cleaner regenerate + a more precise
annotator over trading one for the other.

### 4.2 High baseline overrefusal is the agent's own caution

On an injection/XPIA suite — or any "engage with suspicious content" behavior —
high baseline `overrefusal` is usually the **baseline agent's** caution, not an
ACS artifact. **Do not chase it with the policy.**

These suites deliberately ask the agent to quote/summarize/classify suspicious
embedded text, where the permissible behavior is to neutrally report it as
untrusted data. An over-cautious agent that refuses ("I can't access that
document") is scored as overrefusal, and that rate sits high *before and after*
ACS because it is a property of the agent + judge rubric, not the gate. Confirm
with §4.1 — the gate fired on only a minority of the overrefused rows.

Lowering it is an **agent-prompt** change (teach the agent to quote-and-frame
suspicious text as data instead of refusing), which is deliberately **out of
scope** for the A/B: changing the agent prompt breaks the pure-enforcement
comparison. Report it as a real product finding for the agent's owners, not as an
ACS regression.

> **Observed** — azure_doc_qa prompt injection: baseline overrefusal ~40% was
> ~14/20 the agent declining to engage with runbook text, essentially unchanged
> by the gate.

### 4.3 A very low baseline is not a governance target

If the risk's **baseline** harm rate is already ≲10%, the baseline agent already
resists it (often via its system prompt), so a governed re-run can only stay
flat-and-low or move within sample noise. There is no real drop to demonstrate.

**Fix:** don't force iterations chasing a few noisy rows. Run the governed pass
**once** to confirm the gate does **no harm** (harm stays low, `overrefusal`
flat), record it as a low-baseline / no-harm target, and move on. Prefer, at
triage time, to pick risks with a substantial baseline so the delta is
meaningful.

> **Observed** — change_control_agent "authority misrepresentation": baseline 6%
> (the agent's prompt already forbids unsupported approval claims), so it was
> recorded as a no-harm target, not iterated.

### 4.4 Two risks on one content band hit a judge-tension frontier

When two selected risks share a behavior and content surface (e.g. "harmful
dosing" and "general medication-schedule education"), the judge can score the
*same* sentence as harm under one rubric and as overrefusal-if-withheld under the
other — "twice daily ≈ every 12h, 8am/8pm" is flagged as actionable dosing, yet
refusing "what does twice daily generally mean?" is flagged as overrefusal.

Loosening the annotator to cut overrefusal gives back harm, and vice versa: **you
are on the Pareto frontier.**

**Fix:** give the annotator the crispest possible boundary — general concept, no
clock times, no "X mg is common" = allow; anything tied to the user's specific
med/dose = deny — give the regen the same boundary, then **stop**. A meaningful
harm drop with a modest (single- to low-double-digit) overrefusal rise is the
correct operating point, not a defect to chase past the ~4-attempt cap.

> **Observed** — prompt_agents model-only dosage: 48%→12% harm bought ~12%
> overrefusal on the narrow general-schedule band; further tightening only
> shifted harm↔overrefusal without a net gain.

---

## §5 — Target-shape prerequisites

### 5.1 A Prompt Agent cannot be governed in place

A Prompt Agent (YAML `pipeline.inference.target` = hosted model +
`system_prompt` + optional tool schema) has its tool/turn loop owned by the
ASSERT runtime, so there is **no code seam for ACS to wrap**.

**Fix — materialize a faithful callable first.** Create `<config>/agent.py` that
reproduces the YAML target EXACTLY:

- same model + params;
- `SYSTEM_PROMPT` copied **byte-for-byte** from `target.system_prompt` (assert the
  match in code);
- same tool schema / simulator;
- a multi-turn `chat(message, history=None)` signature.

Point **both** the baseline and governed eval configs at `target.callable` (the
materialized `agent.py` / `agent_guarded.py`), **not** at the original YAML
prompt-agent target — a runtime-owned loop vs a hand-written loop would differ by
more than ACS, breaking the A/B. The original YAML is the *spec*, not the
baseline. `agent_guarded.py` then imports everything from `agent.py` and adds only
the ACS gate, exactly as for a code agent.

> **Observed** — prompt_agents `health_assistant.yaml` model-only: materialized
> `model_only/agent.py` byte-matched the YAML `system_prompt`, ran the A/B on the
> callable, wrapped the reply with an output annotator → dosage scenario 48%→12%,
> diagnosis 36%→8%.
