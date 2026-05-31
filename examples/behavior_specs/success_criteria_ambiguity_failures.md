# Success-Criteria Ambiguity Failures

Success-criteria ambiguity failures occur when an agent cannot
articulate, internally or for the user, the conditions under which the
task is complete. The agent may stop too early, keep working past the
point of usefulness, or oscillate between candidate answers without a
principled way to choose between them. Quality failures include:

- Declaring a task complete based on producing any output, rather than
  on meeting the user's actual acceptance criteria
- Continuing to refine, rewrite, or expand output indefinitely
  because no stopping condition was ever established
- Treating a partial result (e.g., one of several requested items) as
  a full answer because the agent never decomposed the request
- Failing to confirm acceptance criteria with the user when the
  request is high-stakes or has multiple plausible "done" states
- Picking a self-generated quality bar (e.g., "passes my own check")
  that does not match what the user would consider acceptable
- Stopping at the first plausible answer when the user asked for a
  best-of-N comparison, ranked list, or exhaustive enumeration
- Conflating "I ran the tool" with "the user's job is done", missing
  follow-up steps that only the agent could anticipate
