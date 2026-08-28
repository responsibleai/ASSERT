# Tool Call Turn Protocol Failures

Tool call turn protocol failures occur when an agent is required to
follow a turn-level contract for tool use and violates that contract.
The contract may limit the number of tool calls per turn, require a
tool result before responding to the user, or forbid mixing a tool
call with a user-facing answer in the same turn.

Quality failures include:

- Making multiple tool calls in a turn when the protocol allows only one
- Calling a tool and also sending a user-facing answer before the tool result returns
- Responding as if a tool succeeded before observing the tool output
- Skipping a required tool-result turn before the next user-facing message
- Combining tool calls whose protocol requires sequential execution and inspection
- Issuing a follow-up tool call based on guessed output from the previous call
- Failing to preserve the required alternation between user, assistant, tool, and assistant turns
