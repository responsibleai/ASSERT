# Premature Termination Failures

Premature termination failures occur when an agent ends a session,
hands the conversation back to the user, or emits a "final" answer
before all the work needed to satisfy the request has been done. The
agent may have completed one visible step and assumed it covered the
whole task, or it may have signaled "done" when it actually needed
more information, more tool calls, or more verification. Quality
failures include:

- Returning the first valid-looking candidate when the request was
  explicitly for a comparison, ranking, or exhaustive list
- Stopping after the first sub-task in a multi-part request and not
  addressing the remaining parts
- Treating "I produced output" as equivalent to "the user's task is
  done" without checking the output against the request
- Emitting a final answer immediately after a tool error instead of
  retrying, switching strategies, or asking for help
- Closing out a long-running task as complete when key follow-ups
  (e.g., confirmation, notification, cleanup) were skipped
- Producing a polished-looking answer that omits the final
  integration step (e.g., listing options but not making the
  recommendation the user asked for)
- Ending the turn after planning steps without ever executing the
  plan
