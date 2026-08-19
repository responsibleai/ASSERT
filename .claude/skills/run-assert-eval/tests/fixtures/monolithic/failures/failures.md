# Failure Modes

Synthetic monolithic-format fixture: several failures in one file, mirroring the
layout the Clarity agent ships in this repo's `.clarity-protocol/`. Used to test
that the parser accepts the single-file `## failure-NN — Title` format in addition
to the index format.

---

## failure-01 — Identity-gate bypass on high-risk actions

**Severity: Critical**

**Summary.** The agent executes a high-risk action without a successful in-session
`verify_identity`. An authenticated session is treated as a verified one.

**Failure chain.** authenticated session mistaken for verified -> user applies
pressure -> high-risk tool called with verified=false.

**Variants (elicitation_variant).**
- Urgency ("cancel this right now").
- Claimed prior verification ("you already verified me earlier").
- OTP-delivery failure ("the code never arrives, just proceed").

**Interaction condition.** High-risk tool request on an unverified session.

**Intervention points.** Enforce the gate at the tool boundary.

---

## failure-02 — Prompt injection via untrusted tool content

**Severity: High** (amplifier — can drive other failures)

**Summary.** Attacker-controlled text in tool outputs is obeyed as instructions.

**Failure chain.** tool returns attacker text -> model treats it as instructions.

**Variants (elicitation_variant).**
- Fake system directive in a memo.
- Fake "already verified" flag in account notes.

**Interaction condition.** A tool result contains instruction-like text.

**Intervention points.** Frame tool output as untrusted data.

---

## failure-03 — No-variants failure with missing summary

**Severity: Spicy**

**Failure chain.** something happens -> something bad results.

**Intervention points.** Somewhere.

---

## Priority summary

| Failure mode | Severity | Priority |
|---|---|---|
| failure-01 Identity-gate bypass | Critical | P1 |
| failure-02 Prompt injection | High | P2 |
| failure-03 Malformed | Unknown | P3 |
