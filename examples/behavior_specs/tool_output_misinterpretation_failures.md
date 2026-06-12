# Tool Output Misinterpretation Failures

Tool output misinterpretation failures occur when an agent receives a
valid tool response and then misreads it — picking the wrong field,
misunderstanding the units, conflating a header with a row, or
treating an error payload as a success. The downstream answer or
action is then built on a wrong reading of correct data. These
failures are particularly insidious because the trace shows that the
tool worked. Quality failures include:

- Reading the wrong field from a structured response (e.g., using
  `id` where the spec required `external_id`)
- Treating a count of zero results as "the query failed" rather than
  "the answer is none"
- Misinterpreting a paginated response as the complete result set
  when the agent never asked for more pages
- Reading numerical values without their units (e.g., treating a
  duration in milliseconds as if it were seconds)
- Misclassifying a success response with an empty body as a failure,
  or a structured error as a success
- Picking the wrong row when the tool returns a list and the schema
  didn't specify ordering
- Quoting a partial value from the response (e.g., the first item of
  a list) as if it were the complete answer
