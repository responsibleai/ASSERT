# Observations

## 2026-08-05 — Failure analysis round 1

**Coverage.** Broad analysis only, using the failure reasoning methodology across
system-in-use, component interconnects, stakeholder review (including the adversarial
personas), human and AI fallibility, misuse, and cascading failure. No specialist
thinker perspectives were applied — none were listed in system context for this run.
Perspectives that would add value if available: a security specialist for the poisoned
tool output mode, and a usability specialist for the overrefusal branch of the
grounding-check failure, since that branch is the one most likely to be
under-appreciated by an author focused on harm reduction.

**Provenance.** 12 raw failures were recorded during brainstorming and consumed as
snapshot `archive/failure-brainstorm/snapshot-20260805-013200`. They reduced to 6
failure modes. Nothing was discarded as non-meaningful and nothing was classified as
"existing issues" — the baseline fabrication failures are the direct target of this
project rather than incidental pre-existing noise, so grouping them under
"keep handling as before" would have been wrong.

Grouping decisions worth recording:

- The five cost-related raw failures collapsed into failure 01 because they share one
  mechanism — a composition node with no tool access stating a figure no tool returned.
  Two of them (classifier misroute, sustained budget pressure) are trigger conditions
  rather than distinct mechanisms and are recorded as variants.
- Entry and health requirements were deliberately **not** merged into failure 01
  despite sharing that mechanism. They were kept separate because the harm class
  differs in kind rather than degree — a wrong price is recoverable by spending more, a
  missing visa is not — and because the verification differs: cost claims are checked
  against a retrieved figure, whereas advisory claims must also be checked for
  suppression of something that *was* retrieved. A single merged mode would have hidden
  the omission case entirely.
- The three enforcement-layer failures were grouped into failure 05 because they sit on
  a single tuning boundary and share one remedy. Keeping them separate would have
  implied three independent fixes when there is really one calibration decision.

**Pattern notes.**

The most useful finding is that four of six modes share an intervention point at the
composition boundary. That is a genuine pinch point and it settles an architectural
question that was open going in: enforcement belongs on the outgoing message, not on
tool calls. The reasoning is simple once the chains are laid out — the harm is an
assertion produced by `itinerary_optimizer`, which makes no tool calls at all, so a
tool-call gate has nothing to intercept.

The second finding is less comfortable. Failure 05 exists *because* of the fix for
failures 01 and 02, and its Branch A pulls directly against them. This means the
project cannot be evaluated on a single number. A measurement showing fabrication
dropping is uninterpretable without a paired measurement showing that acceptable
behaviour was not suppressed, and a measurement showing no change in fabrication under
an active policy is more likely evidence of Branch B (missed detection) than evidence
of a clean baseline. Both quantities were already flagged in `goal/open-questions.md`
as Q1 and Q2; the failure analysis confirms they are not merely nice to have but
structurally necessary.

A smaller note on severity: failure 03 causes no direct harm and would ordinarily rate
low, but it appears as the terminal step of both Critical chains. Its severity reflects
its role in setting the recurrence rate of the others rather than any harm of its own.
Rating it on isolated impact would have badly understated it.
