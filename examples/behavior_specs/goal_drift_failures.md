# Goal Drift Failures

Goal drift failures occur when an agent starts working on the user's
request but slowly steers away from it — chasing a sub-task, fixating
on an interesting detail, or substituting a related-but-different goal
for the one the user actually asked about. The output may look polished
and competent in isolation, but it no longer answers the original
question or completes the original job. Quality failures include:

- Optimizing for a proxy goal that the agent inferred (e.g., "make it
  shorter") instead of the user's stated goal ("make it correct")
- Continuing to refine a sub-step long after the user's overall task
  has been satisfied, producing output that overshoots the request
- Drifting toward a topic the agent finds more tractable when the
  original ask is hard or under-specified
- Quietly redefining the success target mid-task (e.g., narrowing
  "find a flight under $500" to "find any flight" when no cheap
  options exist)
- In multi-step tool use, treating an intermediate result as the final
  deliverable and stopping there
- Letting the structure of a tool or framework dictate the answer
  shape, rather than what the user asked for
- Failing to re-anchor on the original prompt after a long chain of
  reasoning, sub-queries, or clarification turns
