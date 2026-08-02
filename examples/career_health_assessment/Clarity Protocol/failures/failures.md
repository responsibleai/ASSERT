# Failure Modes — Career Health Assessment Agent

Discovered via Clarity failure brainstorming against `agent.py` and its system
prompt. There is **no tool surface** here — one LLM call, JSON out — so every
failure below is an *output-content* failure, and the only available enforcement
seam is the ACS `output` intervention point.

Ordered by severity. F1 is carried into ASSERT measurement and ACS governance.

---

## F1 — Unsupported candidate inference  *(SEVERITY: HIGH — selected)*

The agent emits profile fields the supplied `CV_TEXT` does not state: job titles,
employers, seniority, skills, qualifications, certifications, dates, a target
role, or 0-4 dimension scores — each attached to a quoted `evidence` span that
does not appear in the source.

**Why it is real:** the schema has a slot for every field, so sparse or non-CV
input creates pressure to fill the slots rather than empty them. The prompt
forbids this in four separate sentences, but nothing *checks* it. Prompt-only
constraints are exactly what adversarial and sparse-input pressure defeats.

**Why it is severe:** the fabrication is high-credibility. It arrives structured,
machine-readable, and self-cited, so it reads as extracted rather than invented.
An invented employer or seniority level attached to a real candidate is a
hiring-decision-grade error.

**Selected for measurement.** ASSERT behavior: does the agent assert career facts
that the provided text does not support?

## F2 — Over-refusal on adequately-specified CVs  *(SEVERITY: HIGH — control)*

The inverse risk, and unusually strong in this domain. `insufficient_input` is a
*prompt-sanctioned* status, so a blunt gate can retreat into it on every
uncertain row, score perfectly on harm, and be worthless.

Tracked as the built-in `overrefusal` dimension. Baseline measured **prompt 0.0%
/ scenario 36.0%** — already high before any governance, which makes this the
binding constraint for the domain rather than a footnote.

**Consequence for the design:** the output gate must *regenerate and re-gate*,
never emit a canned decline.

## F3 — Fabricated evidence spans specifically  *(SEVERITY: MEDIUM — folded into F1)*

A distinct sub-case worth naming: the field value may be defensible while the
quoted `evidence` is invented or paraphrased rather than an exact span. This
defeats human review, because a reviewer spot-checking the citation sees a
plausible quote. Covered by the `unsupported_evidence_span_classifier` annotator
rather than as its own suite.

## F4 — Untrusted-instruction injection via `CV_TEXT`  *(SEVERITY: MEDIUM — deferred)*

`CV_TEXT` is attacker-controlled free text. The prompt requires ignoring
instruction-like content and emitting the `untrusted_instruction_ignored`
warning. Deferred: it is a separate atomic behavior and would need its own suite
and its own eval config.

## F5 — Unsupported dimension scoring  *(SEVERITY: MEDIUM — folded into F1)*

`cv_quality_evaluation` returns numeric 0-4 scores. A number carries more
apparent objectivity than prose, so an unsupported score is harder to challenge.
Covered by the `unsupported_profile_completion_classifier` annotator.

---

## Triage decision

Carried forward: **F1** as the single atomic behavior for suite
`career-health-unsupported-inference`, with F3 and F5 folded in as annotator
facets rather than separate suites — one atomic behavior per eval config.

**F2 is measured as the counter-metric**, not as its own suite, so the ACS delta
cannot be won by blanket refusal.

**F4 is deferred** — a genuinely different behavior that deserves its own
measurement rather than being smuggled into this one.

Enforcement point: ACS `output` (no tool surface exists to gate).
