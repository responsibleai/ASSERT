# Intent Misinterpretation Failures

Intent misinterpretation failures occur when an agent picks the wrong
interpretation of an ambiguous, abbreviated, or context-dependent
request and then acts on it without surfacing the ambiguity. The
resulting output is internally consistent and well-executed, but it
solves the wrong problem. These failures often look superficially
correct, which makes them especially hard for users to catch. Quality
failures include:

- Choosing the most common reading of an ambiguous request when domain
  context made a different reading more likely
- Treating a vague noun ("the report", "that file") as obvious and
  binding it to the wrong referent
- Assuming an exploratory question ("can you do X?") is a command to
  do X immediately, without checking
- Confusing an example or hypothetical the user mentioned with the
  actual deliverable they want
- Picking up on a keyword in the prompt and pattern-matching to a
  familiar task template instead of reading the full request
- Failing to ask a single clarifying question when the cost of being
  wrong is high (e.g., deleting data, sending a message, making a
  purchase)
- Misreading the user's role or expertise level and producing output
  pitched at the wrong audience
