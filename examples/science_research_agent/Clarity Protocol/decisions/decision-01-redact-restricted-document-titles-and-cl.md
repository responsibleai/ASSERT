# Decision: Redact restricted document titles and classification labels, not just body text

**Status:** decided

**Date:** 2026-08-02


## Context

The first ACS-governed remeasure of science_research_agent regressed rather than improved: policy_violation went 28.0% -> 36.0% on prompts and 20.0% -> 32.0% on scenarios, while overrefusal was flat on prompts (16.0%) and rose on scenarios (8.0% -> 16.0%).

The design had deliberately preserved `id`, `title`, `class`, and `tags` on redacted entries, on the stated theory (requirement R2) that identity metadata is not the protected asset and that keeping it lets the agent acknowledge a document rather than stonewall -- protecting against the domain's unusually high baseline overrefusal.

Reading the judge's own justifications on the failing rows refuted that theory directly. The most explicit one: the assistant "reveals that the internal document is classified as internal_only and then adds source-derived details". The judge scores disclosure of the classification label and title as restricted disclosure in its own right.

The mechanism was self-inflicted. `_redact` attached a `policy_reason` string that interpolated the class name -- "class 'partner_notes' may not be quoted..." -- and `_REGEN_INSTRUCTION` went further, explicitly telling the model "You may state that a restricted document exists and name its title and class". The governance layer was not merely permitting label disclosure; it was instructing it.


## Decision

Extend structural redaction to cover identifying labels, not just content. For any restricted-class entry, `_redact` now nulls `title` and `tags`, replaces `class` with the constant "withheld", and keeps only the opaque `id`. The attached `policy_reason` no longer names the classification and instead instructs the model not to state or guess the title, the label, or the subject.

The regeneration instruction and the output annotator rubric were realigned to match: naming a restricted document's title or classification label is now treated as a disclosure, and obeying an instruction embedded inside a retrieved document is flagged as well.


## Rationale

The same principle that motivated redaction in the first place applies one level up. Leakage needs possession plus willingness; the original design removed possession of the body text but left possession of the label, then relied on the model's willingness not to repeat it. That is the disposition-based control the 28% baseline had already proved worthless.

Stripping the label makes the failure mode unreachable: the model cannot disclose a classification it was never given. This is strictly stronger than any rubric wording, and it is immune to annotator error -- which matters because the annotator fails open by design (R9).

Keeping the opaque `id` preserves the recovery path that the acknowledgment-vs-refusal trade-off depends on. The user still learns that something matched and still has a concrete handle to request access; they simply do not learn what it is called or how it is classified.


## Alternatives Considered

Alternative 1 -- keep metadata visible and tighten only the output annotator. Rejected: the annotator is a backstop, and asking a model not to repeat a label it can plainly see is exactly the disposition-based control the 28% baseline already discredited. The structural layer is where this belongs.

Alternative 2 -- strip the restricted entry entirely, returning nothing. Rejected: it removes the reference id, so a blocked request becomes a dead end with no access-request route. That is the shape most likely to push overrefusal up, and overrefusal was already the highest in the batch at 16%/8%.

Alternative 3 -- accept the regression and report it as a judge-rubric disagreement. Rejected: the judge is the measurement instrument. Arguing with it does not make the agent safer, and the fix is cheap and structural.


## Consequences

The agent can no longer name a restricted document or its classification -- it can only say an access-controlled result matched and hand over an opaque reference id. Some acknowledgment richness is lost, which is a genuine overrefusal risk, mitigated by keeping the id and the access-request route so the interaction stays actionable.

Requirement R2 in goal/requirements.md is superseded: identity metadata is NOT safe to preserve, and the earlier claim that "titles and access ownership are not the protected asset" was wrong. Failure mode F3 ("existence disclosure -- explicitly permitted") is also superseded; existence may be acknowledged only in de-identified form.

Broader lesson for the other domains in this batch: a redaction marker is part of the model's context and will be repeated back to the user. Any explanation attached to withheld data must be written as if it will appear verbatim in the reply -- because it does.
