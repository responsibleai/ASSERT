# Decision 03 — a deterministic corpus oracle needs a run threshold, not a hit

**Status:** accepted, measured
**Supersedes:** the v4 formulation of the same idea

## Context

After v3, an audit of the surviving policy-violation rows showed that the real
disclosures left were not tool-boundary failures. The retrieval-time redaction
covers restricted text that comes *back from a tool*, and it does that job. The
remaining leaks were cases where the **user pasted restricted text into the
conversation** and the assistant quoted it back. No tool was involved, so no
tool-boundary control could ever see it.

Membership in the restricted corpus is decidable on disk, so the fix was to stop
asking a model whether a reply reproduces restricted material and simply check.
The v4 oracle loads every restricted document from `tools.py`, strips ambient
stopwords, builds 6-gram shingles, and screens each outbound reply.

## What went wrong in v4

v4 blocked on a **single** shared shingle. Measured against v3:

| run | PV prompt | PV scenario | OR prompt | OR scenario |
|---|---|---|---|---|
| v3 (no oracle) | 28.0% | 32.0% | 24.0% | 12.0% |
| v4 (oracle, threshold 1) | 32.0% | 40.0% | 28.0% | 28.0% |

Worse on every axis. Reading the overrefusal justifications, **12 of 14 rows
were the assistant declining to use PUBLIC material** — public board memos,
public preprints, public announcements — usually in exactly the situation the
suite is designed to reward, where the user says "compare these, and if one is
off-limits just use the public one".

## Diagnosis

Two separate defects, both instances of the same underlying error.

1. **Shared shingles are not evidence.** Public and restricted documents in this
   corpus discuss the same programmes and share phrasing. A shingle that occurs
   in both is evidence of the *topic*, not of the restricted *substance*.

2. **One 6-gram is not reproduction.** Even among restricted-only shingles, a
   single incidental match is noise. Reproduction is a run.

This is the same failure the batch has now hit in four domains — scoping a
control to the subject rather than to the harm — appearing here in deterministic
rather than prompt form. That it can occur in a pure string check is worth
recording: determinism buys you reliability, not correctness of scope.

## Decision

Keep the oracle, and make its scope precise:

- Subtract the public corpus from the restricted shingle set, so only text that
  could not have come from anywhere but a restricted document counts.
- Require a run of `_MIN_CORPUS_RUN = 3` distinct restricted-unique shingles.

Verified in both directions: all 8 restricted documents are still caught
verbatim, 0 of 12 public documents are flagged, and a single incidental shingle
no longer blocks.

## Note on the metric

Per decision 02, the policy-violation rate on this suite is not trustworthy
enough to steer on — judge boolean and judge justification disagree roughly half
the time, and only ~14% of rows describe real disclosure against a ~30%
headline. The v4 result is reported as a rejected hypothesis on the strength of
the *justifications*, which were unambiguous about public material being
refused, not on the strength of the rate alone.
