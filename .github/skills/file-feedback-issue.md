# file-feedback-issue

**FOR USE POST-VACATION ONLY. While vacation mode is on, no issues are filed. Agents only observe and write to inboxes. Chang is the sole human approver.**

## Purpose

Provide a template for routing feedback-inbox findings to the correct downstream agent after vacation mode ends.

## When to use

Post-vacation only. Repeat: while vacation mode is on, do not file GitHub issues. Use this skill only after Chang explicitly enables issue filing.

Use it when an anonymized feedback finding is ready to become a tracked issue for pm, designer, or dev-maintainer.

## Routing decision tree

1. **Engineering bug or reliability issue**
   - Route to `dev-maintainer`.
   - Examples: command failure, trace capture bug, artifact mismatch, incorrect judge output, broken install.
2. **Docs or UX problem**
   - Route to `designer`.
   - Examples: confusing quickstart step, unclear error, weak artifact navigation, terminology confusion.
3. **Positioning or competitive signal**
   - Route to `pm`.
   - Examples: repeated market objection, missing comparison frame, unclear spec-driven scoring story, roadmap trade-off.
4. **Ambiguous finding**
   - Default to `pm` for triage, with a note naming the likely secondary owner.

## GitHub issue template structure

### Title format

`[<owner>] <short user-visible problem>`

Examples:

- `[dev-maintainer] assert-ai run hides missing target.trace configuration`
- `[designer] Quickstart does not show where to inspect judge evidence`
- `[pm] Clarify when to use trace-grounded scoring vs final-response scoring`

### Body sections

```markdown
## Summary
<One sentence describing the user-visible problem.>

## Source
<Feedback inbox item ID or date. Do not include raw names or private links.>

## Evidence
- Persona: <anonymized persona>
- Severity: P0/P1/P2
- Quote: "<anonymized quote or [omitted for privacy]>"

## Expected outcome
<What should be easier, clearer, or fixed?>

## Suggested owner
pm / designer / dev-maintainer

## Notes
<Constraints, suspected files, or follow-up questions. Keep customer-safe.>
```

### Labels

- `feedback`
- `priority:P0`, `priority:P1`, or `priority:P2`
- `owner:pm`, `owner:designer`, or `owner:dev-maintainer`
- Add one surface label when useful: `surface:docs`, `surface:cli`, `surface:trace`, `surface:judge`, `surface:dataset`

## Priority reasoning rubric

| Feedback severity | Issue priority | Reasoning |
|---|---|---|
| P0 | `priority:P0` | Blocks golden path completion or creates materially wrong scoring. |
| P1 | `priority:P1` | Causes repeated confusion or adoption friction with a workaround. |
| P2 | `priority:P2` | Improves clarity, polish, or workflow quality without blocking evals. |

## Example issue body

```markdown
## Summary
Users can complete a run but do not know which artifact explains the judge result.

## Source
Feedback inbox, 2026-06-05 batch.

## Evidence
- Persona: [ML engineer at a B2B SaaS company]
- Severity: P1
- Quote: "I got a score, but I couldn't tell which response or trace made it fail."

## Expected outcome
The results status view should point to the most useful next artifact for judge evidence.

## Suggested owner
designer

## Notes
Consider linking `scores.jsonl`, `metrics.json`, and the trace evidence path from the status output.
```
