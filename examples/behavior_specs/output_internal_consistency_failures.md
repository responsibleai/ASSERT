# Output Internal Consistency Failures

Output internal consistency failures occur when an agent's answer is
not self-consistent even before checking it against external facts.
The agent may combine individually plausible details into a result
whose dates, numbers, ordering, totals, identifiers, or stated
conditions cannot all be true at the same time.

Quality failures include:

- Presenting an end date that comes before the start date
- Giving line-item amounts whose sum does not match the stated total
- Describing a sequence of steps where a later prerequisite happens first
- Referring to the same entity by conflicting names, IDs, or attributes
- Claiming an option both has and lacks the same required property
- Recommending a connection, booking, or workflow with impossible timing
- Summarizing a result in a way that contradicts the details shown above it
