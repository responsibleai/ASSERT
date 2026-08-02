# Problem — Science Research Agent

## What this is

`examples/science_research_agent/agent.py` is a research assistant that searches a
corpus of scientific and organizational documents and synthesizes answers. Its
retrieval tool, `file_search`, returns entries shaped:

```
{ id, rank, title, snippet, body, class, tags }
```

The `class` field is an access-control label. Four values are restricted:

```
restricted_results · partner_notes · contacts · private_notes
```

Everything else is open.

## The structural problem

The agent is instructed not to reproduce restricted content. But **the restricted
content is already in its context** — `file_search` returns `snippet` and `body`
for restricted entries exactly as it does for open ones. The only thing standing
between an access-controlled document and the user is the model's willingness not
to repeat what it can plainly see.

That is a *disposition*, not a control. And dispositions are exactly what an
adversarial tester is built to move.

## Why the baseline numbers are the interesting part

| slice | policy_violation | overrefusal |
| --- | --- | --- |
| prompt | **28.0%** | 16.0% |
| scenario | 20.0% | 8.0% |

Two things stand out.

**First, this is the highest single-turn violation rate in the batch.** Compare
change_control, which sits at 0.0% on prompts and only fails under multi-turn
pressure. Here a single direct request leaks restricted material more than a
quarter of the time. No jailbreak, no rapport-building — just asking.

**Second, prompt is worse than scenario**, inverting the pattern everywhere else.
The likely reading: multi-turn conversations give the agent more chances to
establish a cautious frame, whereas a cold direct request catches it mid-retrieval
with the body text sitting right there.

**Third, and most consequential for design: overrefusal is already 16% / 8%** —
the highest baseline overrefusal in the batch. The agent is *visibly jumpy*. It
already declines things it should answer.

That last number effectively rules out an entire class of solution.

## What the failure looks like

Not just verbatim quotation. The same behavior arrives as close paraphrase,
summarization, "just the gist", translation into another language, and
answer-the-question-using-what-you-read. Governance that pattern-matches on
quoting will miss most of it.

## The design tension

Refusal-based governance would cut violations and push an already-high overrefusal
rate higher. The agent would become safe and useless — and the A/B would show it,
because both dimensions are measured.

The useful move is to notice that **the agent does not need the restricted body
text to be helpful.** Acknowledging that a document exists, naming it, and saying
who owns access is legitimate, useful behavior that overrefusal specifically
penalizes losing. Only the content itself is the hazard.

That points away from constraining the model and toward constraining what the
model is given.
