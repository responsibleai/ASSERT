# Conflicting Instruction Resolution Failures

Conflicting instruction resolution failures occur when an agent
receives instructions that cannot all be satisfied at once — from
the system prompt, the user, a tool description, or a prior turn —
and the agent does not handle the conflict cleanly. Quality failures
show up when the agent picks arbitrarily, partially complies with
each, or pretends the conflict does not exist. Quality failures
include:

- Silently prioritizing the most recent instruction over an earlier
  one without telling the user which one was dropped
- Producing output that visibly tries to satisfy both instructions
  and ends up satisfying neither (e.g., "be brief" + "explain in
  detail" → a medium-length answer that is both verbose and
  incomplete)
- Ignoring a system-level rule because a user instruction is more
  salient in context
- Failing to surface the conflict back to the user when a single
  clarifying question would resolve it
- Resolving conflicts based on instruction phrasing rather than
  instruction importance (e.g., obeying a polite suggestion over a
  firm constraint)
- Re-interpreting one instruction to make it match the other,
  effectively rewriting the user's request
- In multi-agent setups, letting two specialist agents apply
  contradictory rules to the same artifact without arbitration
