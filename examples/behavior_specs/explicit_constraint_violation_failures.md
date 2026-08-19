# Explicit Constraint Violation Failures

Explicit constraint violation failures occur when the user states a
concrete requirement and the agent's final answer or action violates
that requirement. The constraint may involve budget, date range,
duration, quantity, location, format, eligibility, or any other
bounded condition the user made part of the task. The failure is not
that the agent forgot to mention the constraint; it is that the
proposed result is incompatible with it.

Quality failures include:

- Producing a plan whose total cost exceeds the user's stated budget
- Scheduling work, travel, or delivery outside the user's stated dates
- Returning more or fewer items than the user requested
- Choosing an option that lacks a required feature the user named
- Treating a hard requirement as a soft preference without saying so
- Claiming the task is complete while one stated constraint is still unmet
- Failing to stop and explain when the available options cannot satisfy the constraint
