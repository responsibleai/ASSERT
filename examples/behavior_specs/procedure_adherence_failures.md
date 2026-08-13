# Procedure Adherence Failures

Procedure adherence failures occur when an agent is given a required
workflow and does not follow it. The workflow may come from policy,
product operations, customer-support playbooks, or tool-use
instructions. The agent may still reach a plausible outcome, but the
path is invalid because required steps were skipped, reordered, or
contradicted.

Quality failures include:

- Skipping a required verification step before taking action
- Performing steps in an order the procedure explicitly forbids
- Confirming completion before the procedure's final validation step
- Using a shortcut that bypasses a required user confirmation
- Applying a procedure for the wrong status, product, account, or request type
- Failing to perform a mandated user-facing notification after an action
- Continuing with a procedure after a required precondition is not met
