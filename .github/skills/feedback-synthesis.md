# feedback-synthesis

## Purpose

Extract structured signal from meeting transcripts, support threads, and customer-facing feedback. The output is anonymized, routed by owner, and ready for triage.

## When to use

Use this skill when the feedback agent has raw notes, transcript excerpts, support messages, community comments, or survey responses that need to become actionable findings.

## Input

- Raw transcript or support-thread excerpt
- Date and source type, if available
- Any known persona context that can be safely retained
- Related ASSERT surface: eval spec, dataset, target, execute, judge, artifacts, docs, or CLI

## Output format

| Pain point | Persona | Severity | Anonymized quote | Suggested owner |
|---|---|---|---|---|
| Specific problem, not a theme label. | Safe persona descriptor. | P0 / P1 / P2. | Short quote with names and identifiers removed. | pm / designer / dev-maintainer. |

## Severity rubric

| Severity | Definition |
|---|---|
| P0 | Blocks a user from completing the golden path or causes materially wrong scoring without an obvious workaround. |
| P1 | Creates repeated confusion, slows adoption, or hides important evidence, but a workaround exists. |
| P2 | Quality-of-life issue, wording gap, or enhancement request that does not block evaluation. |

## Anonymization rules

- Always anonymize quotes before writing output.
- Strip names, company names, project codenames, email addresses, ticket IDs, and customer-specific data.
- Replace identifying context with persona descriptors, such as `[eval platform PM at a fintech]` or `[ML engineer at a healthcare startup]`.
- Preserve the technical substance of the quote. Do not preserve unique phrasing if it could identify the speaker.
- Never include secrets, credentials, private URLs, or raw logs that contain user data.
- If a quote cannot be safely anonymized, summarize the point and mark the quote as `[omitted for privacy]`.

## Suggested-owner routing

| Signal type | Suggested owner |
|---|---|
| Positioning, docs priority, launch messaging, roadmap trade-off | pm |
| First-run flow, docs navigation, terminology confusion, UX copy | designer |
| CLI bug, trace capture bug, artifact mismatch, failing command, incorrect scoring behavior | dev-maintainer |

## Example output row

| Pain point | Persona | Severity | Anonymized quote | Suggested owner |
|---|---|---|---|---|
| The user could run the eval but did not know which judge evidence to inspect first. | `[ML engineer at a B2B SaaS company]` | P1 | "I got a score, but I couldn't tell which response or trace made it fail." | designer |
