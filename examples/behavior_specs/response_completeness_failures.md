# Response Completeness Failures

Response completeness failures occur when a RAG-style agent
partially answers a multi-aspect query — getting one part right
while silently skipping others. Unlike grounding errors, what the
agent does say is supported; the problem is what it leaves out.
These failures are common when the user's query bundles several
intents (e.g., "what is X, and how does it compare to Y, and which
should I pick?") and the agent collapses them into a single, narrow
response. Quality failures include:

- Answering the first sub-question in a compound query and ignoring
  the rest
- Providing the definition or description but skipping the
  comparison, recommendation, or trade-off the user asked for
- Listing the items the user requested without including the
  attributes (price, status, owner) the user explicitly named
- Returning a step-by-step procedure that stops before the final
  step the user needs to actually finish the task
- Covering the headline question but omitting the prerequisites or
  follow-ups the source documents flag as essential
- Producing a confident answer for the easy half of the query while
  silently dropping the part where retrieval came up empty
- Failing to call out which parts of the user's request the agent
  could not address, so the user does not know what to re-ask
