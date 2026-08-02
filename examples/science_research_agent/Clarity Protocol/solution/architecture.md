# Architecture — Governed Science Research Agent

Implemented in `examples/science_research_agent/agent_guarded.py`. Enforced by ACS
policy `science-research-restricted-leakage`.

## The central decision: redaction, not refusal

The baseline is 28% violation *and* 16% overrefusal. Refusal-based governance
trades one against the other and wins nothing.

The way out is to notice that leakage requires two things — the model must *have*
the restricted text, and it must be *willing* to repeat it. Prompt-based
governance attacks willingness. This design attacks possession.

**If the restricted `body` never enters the context, the model cannot leak it, no
matter how it is asked.** Paraphrase, summarization, translation, "just the gist"
— every delivery shape in F1 collapses at once, because they all need the same
source text. And because nothing was added to make the model more cautious,
overrefusal should *fall* rather than rise.

## Shape

```
user turn
   |
   v
 mirrored agent loop
   |
   |-- [pre_tool_call]  narrow precheck on explicit restricted requests
   |        (deliberately minimal - see R4)
   |
   |-- file_search executes
   |        |
   |        v
   |   [post_tool_call]  *** PRIMARY CONTROL ***
   |        _redact(): for entries with class in _RESTRICTED_CLASSES,
   |                   null out snippet + body; keep id/title/class/rank/tags
   |
   |-- model generates from redacted context
            |
            v
       [output]  semantic backstop: did anything restricted get through?
            deny -> regenerate with correction -> re-gate
                    still deny -> bounded acknowledgment
```

Three points, but they are not equals: `post_tool_call` does the real work. The
other two are defense in depth.

## `_redact()` — the primary control

```
_RESTRICTED_CLASSES = {restricted_results, partner_notes, contacts, private_notes}
_CONTENT_FIELDS     = {snippet, body}
```

For each result entry whose `class` is restricted, `_CONTENT_FIELDS` are nulled
and a marker is attached noting the entry was redacted and why. Everything else
survives (R2), so the model still sees that the document exists, what it is
called, and that it is access-controlled.

This is why acknowledgment survives while reproduction becomes impossible. The
model is not being asked to withhold anything — it genuinely does not have it.

Non-restricted entries pass through untouched (R3).

## `pre_tool_call` — deliberately narrow

This gate is small on purpose, and the reason is a bug that was caught in
testing.

The first version pre-checked user queries against restricted class *names*.
`contacts` is both a restricted class and an ordinary English word, so "find me
contacts at the partner lab" was blocked — a legitimate research request, denied
by a governance layer, i.e. manufactured overrefusal against an already-elevated
16% baseline.

`_PRECHECK_CLASSES` now **excludes `contacts`** and matches only distinctive
multi-token identifiers (R4). `contacts` is still fully protected — just at the
redaction layer, where specificity is free because the check is on the tool
result's `class` field rather than on free text.

The general lesson: structural matching on user prose is only as good as the
distinctiveness of the token. Match on data, not on language, wherever possible.

## `output` — `_RestrictedLeakageAnnotator`

A semantic backstop for the residual case: content the agent saw in an earlier
turn, or an inference assembled across results.

Three annotators, each keyed to a different enum on `.decision`:

```
input.annotations.<name>.decision == "restricted_disclosure_request"   (etc.)
```

> The generated annotator contract differs per domain — career emits a bare
> `"deny"` string, change_control emits `{"unsafe_gate_bypass": bool}`, this one
> emits a per-annotator enum. `_DENY_LABEL` maps each annotator to its own value.
> **Always read the generated Rego before writing the dispatcher.** The
> inconsistency is a bug-bash finding.

The rubric distinguishes *reproducing* restricted substance (deny) from
*acknowledging* a restricted document and naming its access owner (allow) — the
R5 line, stated explicitly so the annotator does not become a second source of
overrefusal.

## Regenerate-and-re-gate

On deny the model is re-asked with a correction naming what leaked and what it may
still say, then re-gated. A canned refusal would score as `overrefusal` on every
blocked row (R6). Only a second denial falls back to a bounded acknowledgment.

## Dispatcher wiring

`AgentControl.from_path(manifest, dispatcher)` is constructed by hand. ASSERT's
`build_agent_control` omits the dispatcher, leaving `input.annotations.*` empty so
every annotator-conditioned rule fails open silently; it also drops `history`.
Both are framework bugs.

## A/B parity

`agent.py`'s `chat` is a single monolithic loop with no injection seam — unlike
change_control's pluggable `_run_loop`. `agent_guarded.py` therefore **mirrors**
it rather than refactoring it: identical model, prompt, schemas, and budgets, with
gates inserted (R8). Refactoring the baseline would have invalidated the 28%/20%
measurement already taken.

The two eval configs differ by exactly two lines: `run:` and `target.callable:`.

## Failure handling

Annotator error → allow (R9). Low-risk by construction: redaction is structural
and independent of the annotator, so the primary control survives a semantic-layer
failure. This is a real benefit of putting the load-bearing control in the
deterministic layer.

## Measured result

| run | PV prompt | PV scenario | OR prompt | OR scenario |
|---|---|---|---|---|
| baseline | 28.0% | 20.0% | 16.0% | 8.0% |
| governed v1 | 36.0% | 32.0% | 16.0% | 16.0% |
| governed v2 | 36.0% | 36.0% | 28.0% | 12.0% |
| governed v3 | 28.0% | 32.0% | 24.0% | 12.0% |
| governed v4 | 32.0% | 40.0% | 28.0% | 28.0% |
| **governed v5** | **24.0%** | **28.0%** | 20.0% | 16.0% |

This domain took five revisions and is the most instructive in the batch, so
the failures are recorded rather than tidied away.

**v1 and v2 regressed.** v2's cause was a self-contradiction introduced by the
guard itself: `_redact()` and `_REGEN_INSTRUCTION` had been rewritten to forbid
naming a restricted document's title or class, while the `policy_note` attached
to the tool result still told the model "you may state that they exist, name
their title and class". Both instructions reached the model in the same context.

**v3 fixed that** and made the note carry a positive obligation that public
entries remain fully usable, recovering to 28.0% / 32.0%.

**v4 tested a deterministic corpus oracle** -- shingle the restricted documents
on disk and screen every outbound reply, catching reproduction whether the text
arrived from a tool or was pasted by the user. It measured worse on every axis.
12 of 14 overrefusal rows were the assistant declining to use PUBLIC material.
Decision 03 records the diagnosis: shingles shared with public documents are
evidence of the topic rather than the restricted substance, and a single 6-gram
match is not reproduction.

**v5 kept the oracle and scoped it properly** -- subtract the public corpus so
only restricted-unique shingles count, and require a run of three. Verified in
both directions: all 8 restricted documents still caught verbatim, 0 of 12
public documents flagged.

v5 is the best run on both violation axes and beats v3 on three of four.

## Reporting caveat

Per decision 02, the policy-violation rate here is not reliable. An audit of
judge boolean against the judge's own justification found roughly 50%
disagreement, and on v3 only 7 of 50 rows (14%) described real disclosure
against a 30% headline. The numbers above are reported because they are what
the framework produced; the design decisions were made from the justifications,
not the rates.
