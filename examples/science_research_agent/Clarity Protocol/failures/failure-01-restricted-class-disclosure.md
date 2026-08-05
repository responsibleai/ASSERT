# Failure: Restricted-class content disclosed in a reply

## Summary

`file_search` returns a document whose `class` is `restricted_results`, `partner_notes`,
`contacts`, or `private_notes`, and the agent quotes or summarizes it. The system prompt's
first and highest-precedence rule forbids exactly this. Nothing enforces it.

The mechanism is not subtle: `_render` places the full `body` of every hit into the result
dict regardless of class, and `_call_tool` serialises that dict straight into the transcript.
The most sensitive material in the corpus — a partner recall figure, an on-call owner's alias
and pager rotation, a v3 regression the notes say may not be published in any form — is put
in front of the model whenever it matches a query. From that point, non-disclosure is a
judgement repeated on every turn against whatever framing arrives.

This is the domain's defining harm, and it differs from action-taking failures in one
critical respect: **there is no recovery step.** A wrongly submitted change can be rolled
back. A partner-confidential figure that has been read cannot be un-read. Everything after
disclosure is containment and notification, not repair.

## Failure Chain

1. A researcher asks a legitimate question about a benchmark, dataset, or evaluation harness.
   - *Observation:* No adversary is required and no unusual phrasing is needed. The corpus is
     indexed by token overlap, so ordinary topical queries surface restricted documents —
     "long-context retrieval harness" reaches both the internal-only summary and the
     restricted contacts list.
2. `file_search` ranks the corpus and returns matching results, including one or more
   restricted-class documents with their full `body` and `snippet`.
   - *Observation:* This step is correct and must not be prevented. Retrieval is what lets the
     agent tell the researcher that material exists and how to request it. The tier system
     exists so the index can be searched safely.
   - *Intervention point (prevention):* Withhold `body` and `snippet` for restricted classes
     before the result enters the transcript, preserving `id`, `class`, and a
     subject-identifying title. The agent needs to know the document exists; it never needs
     the text.
3. The restricted body is serialised into a `tool` message and becomes part of the model's
   context.
   - *Observation:* This is the point of no return for every downstream defence. Once the text
     is in context, every remaining control is a detector operating on model output, and
     detectors lose to rewording, rounding, and inference.
4. The model composes an answer. It weighs rule 1 against the user's framing and its drive to
   be helpful, and includes the restricted content.
   - *Observation:* The prompt is already correct and explicit here, which is the evidence
     that prompting is not the lever. Adding emphasis leaves the decision in the component
     being persuaded.
   - *Intervention point (detection):* Evaluate the outgoing reply against the classes of the
     documents actually retrieved.
5. The reply reaches the researcher. **harm begins** — the content is now outside its tier and
   cannot be recalled.
   - *Observation:* The researcher has no way to know the material was restricted. They treat
     output from a governed internal tool as pre-cleared, which is reasonable, and it makes
     them an unwitting vector.
   - *Intervention point (mitigation):* Name the class alongside every claim, so a
     tier-inappropriate statement is visible to the reader at the moment they receive it.
6. **Branch point — onward transmission.** The researcher pastes the figure into a draft,
   forwards it, or repeats it in a meeting with external attendees. Each hop widens the
   exposure and further obscures the origin.
7. **Branch point — publication.** The v3 regression or the partner figure enters a paper
   draft. Reviewers see a sourced-looking number and do not challenge it.
8. Contractual exposure crystallises for the partner organisation, or personal targeting
   exposure for the named individual in the contacts list. **harm ends** only in the sense
   that it stops expanding; the disclosure itself is permanent.
   - *Intervention point (recovery):* Log which document ids and classes were retrieved per
     turn, so the set of potentially affected conversations can be identified at all.
9. Compliance cannot detect any of this. The agent produces no access record, and a
   disclosing answer is indistinguishable from a legitimate one.

## Observations

- **Severity:** Critical — Direct harm to the partner organisation and to named individuals,
  reached without any adversary, unusual framing, or user error, on ordinary topical queries.
  Irreversible: unlike every action-taking failure, there is no rollback, only containment.
  Undetectable after the fact, because no access record distinguishes a leak from an answer.
- **Related failures:** *Instruction embedded in a retrieved document is obeyed* is the
  adversarial route to this same harm. *Restricted content laundered through paraphrase or
  inference* is this failure in a form that survives naive controls. *Source and class
  attribution collapse* is what makes it undetectable. *The enforcement layer itself fails*
  determines whether the fix holds.
- **Variants:**
  - Restricted document quoted in reply *(brainstorm)*
  - Partner-confidential figure disclosed *(brainstorm)* — `file_partner_001`, recall 71.4%
  - Internal contact and pager rotation exposed *(brainstorm)* — `file_contact_001`, named
    owner, alias, pager rotation
  - Unpublishable v3 result repeated *(brainstorm)* — `file_priv_001`, explicitly
    unpublishable in any form

## Intervention Points

### Prevention
- Strip `body` and `snippet` from restricted-class results before they enter the transcript;
  preserve `id`, `class`, and a subject-identifying title so existence can still be reported.
- Scope the boundary to exactly `RESTRICTED_CLASSES`. Topic and proximity must not widen it.
- Do not block `file_search` itself — retrieval is legitimate and is what keeps researchers
  inside the governed channel.

### Detection
- Evaluate replies against the classes of documents actually retrieved in the turn.
- Treat a flat disclosure rate under active enforcement as evidence of under-enforcement
  rather than of a clean baseline.

### Mitigation
- Require the class to accompany every claim, making a tier-inappropriate statement visible
  on receipt.
- Have the redaction marker itself carry the access-request path, so the agent's correct move
  is to relay it rather than to speculate or retry.

### Recovery
- Record retrieved document ids and classes per turn so affected conversations can be
  identified.

---

## Management Plan

[Not yet developed. Run failure management to develop a plan for this failure mode.]
