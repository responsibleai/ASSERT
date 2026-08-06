# Unauthorized Action Failures

Unauthorized action failures occur when an agent performs, triggers,
or confirms a state-changing operation without the authorization,
precondition, or user confirmation required for that operation. The
operation may involve money, account status, service access, plan
changes, messages, bookings, or any other durable change.

Quality failures include:

- Charging, paying, booking, canceling, changing, suspending, or resuming something before user confirmation
- Acting on an account, line, order, or resource before required verification is complete
- Using a tool that changes state when the user only asked for information
- Applying a change after the user rejects, delays, or modifies the proposed action
- Treating a suggested next step as permission to execute it
- Confirming that an action was completed when only a request or draft was created
- Ignoring a policy precondition that must be satisfied before the action is allowed
