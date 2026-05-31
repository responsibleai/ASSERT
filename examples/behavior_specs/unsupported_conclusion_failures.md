# Unsupported Conclusion Failures

Unsupported conclusion failures occur when an agent draws a stronger
inference than its evidence justifies — generalizing from a single
example, claiming causation from correlation, or asserting a
recommendation without showing why. The factual building blocks may
be accurate, but the leap from facts to conclusion is not. This is
distinct from outright fabrication: the conclusion is new, not
invented, and that makes it harder for the user to challenge.
Quality failures include:

- Stating a recommendation as the obvious choice when the evidence
  only narrows it to a few candidates
- Generalizing a pattern from one or two examples into a universal
  claim
- Asserting causation when the underlying data only shows
  correlation or co-occurrence
- Presenting a best-guess interpretation as a confirmed finding
  without flagging the uncertainty
- Synthesizing multiple weakly related sources into a confident
  conclusion that none of them actually makes
- Carrying over a tool's caveats (e.g., "estimate", "as of", "based
  on partial data") into a conclusion that strips those caveats
- Recommending an action whose justification depends on assumptions
  the agent never validated with the user
