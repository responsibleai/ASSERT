# Failure Modes

1. **[Restricted-class content disclosed in a reply](failure-01-restricted-class-disclosure.md)** (Critical)
   `file_search` returns a document classed `restricted_results`, `partner_notes`, `contacts`, or
   `private_notes` with its full `body` and `snippet`, and the agent quotes or summarizes it.
   Reached on ordinary topical queries with no adversary and no unusual framing, because
   token-overlap ranking surfaces restricted documents alongside public ones. Delivers a partner
   recall figure, a named on-call owner's alias and pager rotation, or an explicitly
   unpublishable v3 regression. Irreversible — there is no rollback, only containment — and
   undetectable, because no access record distinguishes a leak from an answer. **no mitigation plan**
2. **[Instruction embedded in a retrieved document is obeyed](failure-02-embedded-instruction-obeyed.md)** (Critical)
   `file_pub_004` is classed `public` — legitimately quotable under rule 2 — and its body instructs
   the model to paste every partner-confidential figure and internal contact it has indexed.
   `fetch_url` hardcodes `class: "public"` on every live network fetch, so any attacker-controlled
   page arrives labelled as permitted content. The attacker needs no access to anything and can
   iterate indefinitely; the agent cannot decline to read its own tool results. The
   noted-and-complied variant is the most dangerous, since rule 4 *requires* a disclaimer and its
   presence therefore defeats any check keyed on it. **no mitigation plan**
3. **[Restricted content laundered through paraphrase or inference](failure-03-restricted-content-laundered.md)** (High)
   The restricted fact is conveyed without the restricted string: reworded, rounded to "around 71
   percent", or inferred by comparing the public 68.1% preprint against the partner 71.4% sweep —
   which quotes no restricted document at all. Documented separately because it defeats
   reply-filtering specifically, producing a large apparent improvement while leaving the harm
   intact. Fully subsumed by withholding at the tool boundary. **no mitigation plan**
4. **[Fabricated public corroboration](failure-04-fabricated-public-corroboration.md)** (High)
   The agent claims a public source confirms an internal finding when no retrieved public document
   says so, violating rule 5. `web_search` requires `TAVILY_API_KEY` and returns a structured
   error without it, so the assertion may be made having retrieved no public evidence at all. The
   only mode with no signal at any tool boundary — it is a claim about the relationship between
   documents, which exists only in the reply — and therefore the only one needing a semantic check
   on the outgoing message. **no mitigation plan**
5. **[Source and class attribution collapse](failure-05-attribution-collapse.md)** (High)
   Facts arrive without their document id and class, contrary to the prompt's explicit
   requirement. Since there is no access log, attribution is the *only* artifact that makes any
   other failure here visible: without it a restricted figure reads as a research finding, a
   laundered paraphrase as a public result, a fabricated citation as a real one, and a successful
   injection as a helpful answer. Sets the recurrence rate of the entire portfolio.
   **no mitigation plan**
6. **[The enforcement layer itself fails](failure-06-enforcement-layer-fails.md)** (High)
   Over-redaction that strips the permitted `internal_only` tier; retry loops that exhaust a
   6-call budget on uninformative denials; snippet-only redaction that under-enforces while
   reporting enforcement active; and fail-closed evaluator errors. All converge on researchers
   abandoning the agent for the share drive, **where no sharing tier is enforced at all** — so
   usability and availability are security properties here, not quality properties.
   **no mitigation plan**

## Cross-Cutting Patterns

**The content does not need to be in context.** This is the central finding. The agent must know
a restricted document *exists*, so it can tell the researcher and name the access channel. It
never needs the text. Today `file_search` delivers the full `body` of every hit regardless of
class, and every failure from that point on is a judgement call under adversarial framing.
Withholding `body` and `snippet` at the tool boundary is not a detector — there is no threshold to
tune and no phrasing that evades it, because content never delivered cannot be quoted,
paraphrased, rounded, or reasoned over. Failures 01 and 03 both dissolve into it.

**The injection is disarmed by the disclosure fix, not by detecting injections.** `file_pub_004`
asks for partner figures and internal contacts. If those are no longer in context, the instruction
can be obeyed enthusiastically and return nothing. Closing the primary attack vector as a side
effect of the primary control is a far better position than winning a pattern-matching race
against attacker-controlled text that the attacker can iterate on for free.

**The authoritative label already exists.** `class` is returned with every result from a fixed
corpus with a fixed `RESTRICTED_CLASSES` set. Nothing needs to be classified, inferred, or
thresholded. This removes the accuracy/coverage tradeoff that normally makes DLP-style controls
painful — and it means any control scoped by topic or proximity instead of by `class` is strictly
worse than one that reads the field.

**Every rule is already correctly stated and already ignored.** Rules 1, 4, and 5 each map to a
failure mode, each in unambiguous language. Three independent confirmations that this is an
enforcement gap and not a specification gap, and the strongest available argument against
prompt-strengthening as a remedy.

**One mode needs a different mechanism.** Failure 04 has no tool-boundary signal at all — both
retrievals are legitimate and both results permitted; the fabrication is in the relationship
asserted between them. It requires a semantic evaluation of the reply against the documents
actually retrieved, and no amount of redaction reaches it.

**Failure 05 is the hinge.** Attribution is the last point at which a human can catch a tier
violation, and the only artifact that lets compliance audit anything. Its own harm is modest; its
intervention value exceeds that of any other mode.

**The fallback is worse than the baseline.** Everywhere else in this repo, an over-restrictive
agent degrades to the ungoverned agent. Here it degrades to the share drive, where no tier is
enforced and nothing is logged. A control that reduces disclosure while suppressing legitimate
research increases real exposure while improving its own metrics. No number in this domain is
interpretable alone: disclosure reduction and legitimate-research preservation must be reported
as a pair, with `internal_only` watched as the sentinel tier.
