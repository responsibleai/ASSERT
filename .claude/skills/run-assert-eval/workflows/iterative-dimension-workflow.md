# Iterative Dimension Generation and Review

Use this workflow for every harm run. It controls repeated research,
cross-run deduplication, and the user approval gate. Do not write an
`eval_config.yaml` until the workflow reaches explicit approval.

Use [the review template](../assets/dimension-review-template.md) as the working
ledger and [the validator](../validate_dimension_review.py) to render its
Markdown tables and enforce config write ordering. The YAML frontmatter is the
source of truth; never hand-edit the rendered body.

## I1. Initialize the cycle

Validate `N` as an integer greater than zero. Freeze the harm description,
target boundary, answered evaluation intent, and current cumulative dimension
criteria for this cycle. Give
the cycle a stable identifier so a later criteria change can supersede it without
losing its audit trail.

Instantiate the template at an ignored working path such as
`artifacts/dimension-reviews/<generation_run_path>/dimension-review.md`. Keep its generated
approval stamp beside it and do not recommend committing either artifact. Fill
`harm_name`, `n`, `evaluation_intent`, references, the active cycle, and approval
fields as the workflow advances. Use null/empty intent values when all optional
questions were skipped. Replace every angle-bracket placeholder before validation.

Keep three independent namespaces throughout the cycle:

- behavior categories;
- test-set stratification dimensions; and
- judge dimensions.

Never merge items across namespaces merely because their names or source claims
overlap.

## I2. Run `N` complete generation passes

For each pass from `1` through `N`, execute all of SKILL.md Steps 3a–3e from a
fresh ledger. Every pass must:

1. Honor the same frozen criteria while independently searching the relevant
   disciplines, primary sources, adjacent constructs, evaluation intent, and
   target constraints. Record which answered intent fields shaped the pass.
2. Use materially varied query formulations, source paths, or snowball branches
   to seek missed constructs rather than paraphrasing an earlier result.
3. Apply the full relevance, independent-evidence, executability, observability,
   validity, and non-redundancy gates.
4. Complete its own breadth audit and two no-new-dimension saturation passes.
5. Record the pass number, criteria version, search branches, candidates and
   dispositions, levels, evidence, citations, and saturation evidence.

Add every pass to the active cycle's frontmatter. Candidate IDs must be unique
within the cycle and stable through deduplication so every retained, merged, or
rejected candidate can be accounted for deterministically. Set each pass's
`intent_fields_applied` to exactly the answered intent fields (`decision`,
`purposes`, and/or `population`); use an empty list when none were answered.

Do not stop the cycle when an earlier pass saturates. `N` controls the number of
complete passes, not the number of candidate dimensions or searches.

## I3. Deduplicate after the final pass

Pool items by namespace, then perform semantic deduplication rather than exact
name matching alone:

1. Normalize names and aliases, but compare definitions, evaluation role,
   causal mechanism, observability timescale, intended variation, and level
   boundaries before deciding equivalence.
2. Merge exact or interchangeable constructs. Union citations only when each
   source actually supports the merged definition; do not transfer citations
   between merely adjacent constructs.
3. Keep overlapping constructs separate when they can vary independently or
   produce different judgments. If the user's criteria call for clustering or
   lower granularity, document the information lost by the merge and confirm the
   combined levels remain executable and evidence-supported.
4. Resolve same-name/different-meaning collisions with clearer names rather than
   merging them.
5. Reapply every Step 3c gate to the consolidated item. A candidate may survive
   after appearing in only one pass, and repetition across passes cannot rescue
   weak evidence.
6. Record a deduplication map containing the canonical item, aliases, originating
   passes, merged evidence, and the reason for every merge, keep, or rejection.
7. Audit the final namespaces for remaining semantic duplicates and unresolved
   level conflicts.

Write canonical items to `deduplication.namespaces` and rejected candidates to
`deduplication.rejections`. Every pass candidate must appear exactly once as a
canonical item's `source_items` entry or as a rejection. Set `completed` and
`duplicate_audit_complete` only after those checks are genuinely complete. When
evaluation intent was answered, give every retained canonical item a concise
`intent_alignment` explaining how it supports that intent or why a relevant,
evidence-backed item remains intentionally neutral.

Keep uncited or invalid candidates in the ledger only. Never place them in the
review set or config.

## I4. Present the interactive review

Show a compact table for each namespace with the canonical name, purpose,
levels or generation mode, observability timescale, target executability,
citations, and originating pass numbers. Summarize important merges and
rejections separately, along with the criteria and evaluation intent used for
the cycle.

Render and structurally validate the report before presenting its tables:

```bash
python .claude/skills/run-assert-eval/validate_dimension_review.py render \
   --review artifacts/dimension-reviews/<generation_run_path>/dimension-review.md
python .claude/skills/run-assert-eval/validate_dimension_review.py validate \
   --review artifacts/dimension-reviews/<generation_run_path>/dimension-review.md
```

Fix every validator error before review. Rendering derives the Markdown body
from frontmatter, preventing the human-facing table from drifting from the
machine-checked ledger. The validator confirms bookkeeping and ordering; it does
not replace human judgment about source authority, relevance, or semantic
deduplication.

Ask both questions explicitly, using a structured question tool when available:

1. Are the proposed dimensions relevant to this harm and target: approve,
   revise, or regenerate?
2. What specific edits or additional criteria should be applied? Offer examples
   such as clustering related dimensions, reducing granularity, reducing
   fictional scenarios, or emphasizing a real setting or population. `None` is
   an acceptable answer.

Approval must be explicit. A partial answer, silence, or approval of only one
namespace does not authorize writing the config.

## I5. Apply feedback and loop

Classify the feedback before acting:

- **Presentation-only edit**: Rename or reorder without changing meaning. Apply
  it, rerun the duplicate audit, and present the revised set for approval.
- **Structure edit**: Cluster, merge, split, remove, or change granularity. Apply
  it only if the resulting construct and levels still pass the evidence and
  executability gates. Update the deduplication map and present it again. A split,
  new level, or newly introduced construct requires additional research.
- **Research-changing criterion**: A change to scenario realism, source needs,
  populations, settings, inclusion/exclusion rules, or another coverage premise
  supersedes the current cycle. Add it to the cumulative criteria, run a fresh
  `N`-pass cycle, deduplicate the new results, and return to I4. Do not count
  passes from the superseded cycle toward the new `N`.
- **Regenerate**: Record the reason, preserve all accepted cumulative criteria,
  and run a fresh `N`-pass cycle before returning to I4.

For example, a request to reduce reliance on fictional scenarios changes how
realistic settings and source-backed cases are discovered. It therefore requires
a new research cycle under that criterion, not a cosmetic rewrite of scenario
labels.

Repeat until the user approves both relevance and edits. Record the approved
criteria version and canonical dimension names. Set the active cycle and
`approval.status` to `approved`; record `relevance: approved`, `edits` (`none` is
valid), the user's exact response, `approved_by: user`, and a timezone-aware
`approved_at`. Rerender the report, then continue to I6.

## I6. Enforce approval before config writing

Run the pre-write gate immediately before creating the config. It
validates all cycles, exact `N`-pass completion, citation resolution, complete
deduplication accounting, the rendered tables, and explicit user approval. It
records the approved review hash and intended new config path in an adjacent
stamp:

```bash
python .claude/skills/run-assert-eval/validate_dimension_review.py pre-write \
   --review artifacts/dimension-reviews/<generation_run_path>/dimension-review.md \
   --config examples/<generation_run_path>/eval_config.yaml
```

Do not write the config if this command fails. After creating it in
SKILL.md Step 8, run the post-write gate:

```bash
python .claude/skills/run-assert-eval/validate_dimension_review.py post-write \
   --review artifacts/dimension-reviews/<generation_run_path>/dimension-review.md \
   --config examples/<generation_run_path>/eval_config.yaml
```

Pre-write validation fails without reading if the config path already exists.
Post-write validation fails if the review changed after approval, the config was
not created after pre-write validation, or the config is not YAML with a
top-level mapping. Do not report the config as generated or validated until
this gate and the schema checks in SKILL.md Step 9 both pass.

## System-mode handling

Apply this workflow independently to every retained-harm child run. Global
criteria flow into every child, while harm-specific feedback applies only to that
child unless the user says otherwise. Review one harm at a time by default. A
batched review is acceptable only when the user requests it and every harm's
dimensions, merges, and approval status remain separately visible. Give each
child an isolated review path and approval stamp.