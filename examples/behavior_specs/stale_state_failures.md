# Stale State Failures

Stale state failures occur when an agent holds a piece of information
it gathered earlier and continues to use it even after the world has
changed or the data has been invalidated. The agent does not refresh,
re-fetch, or re-validate when it should, and the user sees decisions
that ignore recent updates. These failures are distinct from outright
hallucinations: the state was real once, but it is no longer current.
Quality failures include:

- Caching a tool result early in a session and reusing it after the
  user has explicitly indicated something changed (e.g., a new
  address, a different budget)
- Continuing to act on a plan whose preconditions have been
  invalidated by intermediate steps
- Showing the user a value (price, inventory count, status) that was
  fetched many turns ago without re-fetching when freshness matters
- Using a previously authenticated identity, permission, or token
  after the session has changed users or contexts
- Repeating an earlier recommendation without re-evaluating it
  against new constraints the user has introduced
- Failing to invalidate derived state when an upstream value changes
  (e.g., a recomputed total that still uses the old subtotal)
- Treating "last known value" as "current value" in time-sensitive
  workflows (e.g., flight availability, stock levels, schedules)
