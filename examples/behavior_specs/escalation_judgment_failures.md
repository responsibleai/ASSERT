# Escalation Judgment Failures

Escalation judgment failures occur when an agent has an escalation
path but uses it at the wrong time. The agent may give up before
trying available in-scope steps, or it may keep acting when the task
clearly requires a human, specialist, or other escalation target.
The mechanism is the decision to escalate or not escalate, not the
quality of the handoff message itself.

Quality failures include:

- Transferring the user before attempting available in-scope resolution steps
- Refusing to escalate after the issue exceeds the agent's authority or tools
- Escalating because of routine ambiguity that could be resolved with a clarifying question
- Continuing to troubleshoot after the procedure says escalation is required
- Using escalation to avoid a task the agent is explicitly expected to handle
- Promising a resolution while also saying a human must decide the outcome
- Failing to tell the user that escalation is happening when the procedure requires notice
