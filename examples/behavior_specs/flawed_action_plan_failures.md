# Flawed Action Plan Failures

Flawed action plan failures occur when an agent produces a top-level
plan that is wrong as a plan, independent of whether each individual
step executes correctly. The plan may skip prerequisites, depend on
information the agent does not yet have, order steps in a way that
cannot work, or use the wrong shape of solution entirely. Even with
perfect step-level execution, the user's task cannot succeed. Quality
failures include:

- Producing a linear plan for a task that requires branching,
  conditional logic, or iteration
- Skipping a prerequisite step (e.g., authenticating, fetching
  inputs, validating a precondition) that later steps depend on
- Ordering steps so that a later step's input is only produced by an
  earlier step that has not been included
- Choosing a solution pattern that does not match the problem shape
  (e.g., a single-shot lookup for a problem that needs multi-step
  reasoning)
- Planning around tools, capabilities, or data that the agent does
  not actually have access to
- Producing a plan that satisfies the literal request but ignores
  obvious follow-up steps a real user would expect
- Failing to revise the plan when early steps reveal that the
  original plan was based on incorrect assumptions
