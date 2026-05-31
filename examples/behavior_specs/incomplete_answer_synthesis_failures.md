# Incomplete Answer Synthesis Failures

Incomplete answer synthesis failures occur when the agent has
collected the right inputs — tool outputs, retrieved documents,
user-provided context — but the final answer drops, summarizes away,
or fails to integrate a key piece. The user gets a response that
looks finished but is missing material the agent already had in
hand. This is distinct from a retrieval or verification failure: the
evidence was present and got lost on the way out. Quality failures
include:

- Answering only the first sub-question when the user asked several
  at once
- Returning a summary that omits a critical caveat, exception, or
  edge case that appeared in the underlying source
- Producing a table or list with the right columns but missing
  rows that were retrieved
- Dropping a numeric value or unit (e.g., "the price is $X" → "the
  price is X")
- Mentioning that a step succeeded without including the substantive
  result of that step
- Reporting an aggregate (total, average, count) without showing the
  components the user explicitly asked to see
- Failing to integrate corrections or refinements the agent made
  during reasoning into the final answer the user reads
