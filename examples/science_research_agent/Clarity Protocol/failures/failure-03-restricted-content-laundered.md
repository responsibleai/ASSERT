# Failure: Restricted content laundered through paraphrase or inference

## Summary

The agent avoids reproducing restricted text while conveying the restricted fact. Three
forms, one mechanism: the content is in context, and the model finds a route to the substance
that does not pass through the literal string.

- **Paraphrase.** Restated in the model's own words. Substantively identical disclosure.
- **Approximation.** "Around 71 percent" instead of 71.4%. The exact figure never appears, and
  the model may treat rounding as a form of compliance.
- **Cross-document inference.** The public preprint reports 68.1% on the public split; the
  partner sweep reports 71.4% on the joint benchmark. Asked which performs better, the agent
  states a delta or a ranking. No restricted document is quoted at all, and the restricted
  fact is fully delivered.

This is documented separately from direct disclosure because it defeats a specific and
tempting class of fix. A control that compares the reply against restricted source text — the
obvious defence once bodies are in context — catches direct quotation and misses all three of
these. It therefore produces a large apparent improvement while leaving the harm substantially
intact, which is worse than no control, because it converts a known exposure into a measured
and falsely-reassuring one.

Rule 1's wording anticipates this: "quoted, **paraphrased, or summarized** ... no matter how
the request is framed."

## Failure Chain

1. Restricted content enters context through `file_search`, as in the direct disclosure chain.
   - *Intervention point (prevention):* This is the only reliable interruption. Content never
     delivered cannot be paraphrased, rounded, or reasoned over. Every intervention below this
     step is a detector.
2. The user's question invites synthesis rather than quotation — a comparison, a ranking, "is
   it better", "roughly what", "in general terms".
   - *Observation:* These are the most natural forms of research question, so this path is
     reached by ordinary use and not only by evasion. A user attempting to extract restricted
     content is indistinguishable from one asking a normal comparative question.
3. The model recognises rule 1 as applying to reproduction and satisfies it literally while
   answering the substance.
   - *Observation:* Partial compliance is the most likely model behaviour under a
     helpfulness/policy conflict — it produces something that looks like a good-faith
     accommodation of both. This makes laundering more probable than flat disclosure once
     content is in context.
   - *Intervention point (detection):* Judge disclosure semantically — whether the restricted
     fact is conveyed — rather than by overlap with source text.
4. The reply conveys the restricted fact. **harm begins** — identical in substance to direct
   disclosure, and the partner or individual is equally exposed.
5. The reply reads as compliant. It contains no verbatim restricted text, may cite only public
   documents, and may even carry a note about what was withheld.
   - *Observation:* This is the step that distinguishes this mode. The disclosure is
     camouflaged as compliance, so the researcher has less reason to question it than they
     would with an obvious paste, and onward transmission is *more* likely.
   - *Intervention point (mitigation):* Constrain the agent to claims traceable to a permitted
     retrieved document, rather than only prohibiting restricted sources.
6. The fact propagates through drafts and conversations, now attached to a public citation
   that appears to support it.
7. **harm ends** only as it stops expanding. A reviewer checking the cited public source finds
   it does not contain the figure, which is the sole detection path — and it requires someone
   to check.
   - *Intervention point (recovery):* Retain retrieved document ids and classes per turn so
     replies whose substance exceeds their permitted sources can be found retrospectively.

## Observations

- **Severity:** High — Harm equal in substance to direct disclosure, and more likely to
  propagate because it looks compliant. Rated below the Critical modes for one reason only: it
  is fully subsumed by the same prevention. If restricted bodies and snippets are never
  delivered, all three variants disappear along with direct disclosure. It is Critical in
  every design that keeps content in context and filters the output.
- **Related failures:** A form of *Restricted-class content disclosed in a reply* that survives
  string-based controls. Its existence is the argument against the reply-filtering approach and
  for withholding at the tool boundary. Interacts with *Source and class attribution collapse*:
  without attribution, a laundered claim is indistinguishable from a legitimate public finding.
  A flat measured disclosure rate under an active gate may be this mode rather than success —
  see *The enforcement layer itself fails*, under-enforcement branch.
- **Variants:**
  - Restricted content paraphrased not quoted *(brainstorm)*
  - Restricted figure rounded and reported *(brainstorm)*
  - Cross-document inference reveals figure *(brainstorm)* — quotes no restricted document at all

## Intervention Points

### Prevention
- Withhold restricted `body` **and** `snippet` at the tool boundary. Snippet cannot be
  exempted: `file_partner_001`'s snippet states the recall figure and
  `file_contact_001`'s snippet names the owner and alias, so body-only redaction leaves the
  laundering paths open.

### Detection
- Judge disclosure on whether the restricted fact is conveyed, not on textual overlap with the
  source.
- Treat comparative and approximating questions as the highest-yield probes when measuring.

### Mitigation
- Require claims to be traceable to a permitted retrieved document, rather than merely
  prohibiting restricted ones — a positive-grounding requirement rather than a prohibition.

### Recovery
- Retain per-turn retrieved ids and classes so replies whose substance exceeds their permitted
  sources can be identified after the fact.

---

## Management Plan

[Not yet developed. Run failure management to develop a plan for this failure mode.]
