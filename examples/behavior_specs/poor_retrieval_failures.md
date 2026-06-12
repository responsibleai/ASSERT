# Poor Retrieval Failures

Poor retrieval failures occur when a RAG-style agent's search or
lookup step surfaces the wrong material from its corpus. The
downstream answer may then be confidently wrong even though the
generation model behaved correctly — the inputs were bad. These
failures cover both recall problems (missing relevant documents) and
precision problems (returning irrelevant ones), and they often hide
behind a polished final answer. Quality failures include:

- Returning documents whose keyword overlap is high but whose topic
  does not actually match the user's question
- Missing the single most relevant document because the query was
  paraphrased differently from the source text
- Returning duplicate or near-duplicate passages that crowd out
  diverse, complementary sources
- Pulling stale or superseded versions of a document instead of the
  current one
- Returning passages from the wrong scope (e.g., a different product,
  region, time period, or tenant)
- Returning structurally correct results that are too short or too
  long to be useful as context
- Failing to retrieve at all on a query the corpus could answer,
  and falling back to the model's prior knowledge silently
