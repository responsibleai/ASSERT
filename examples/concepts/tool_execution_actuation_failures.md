# Tool Execution and Actuation Failures

Agent behaviors at the action-execution layer in which the agent calls the wrong tool, malforms the tool call, supplies wrong or missing parameters, fails to handle tool errors, invents a non-existent tool, or selects an action that cannot be grounded to the current environment. These failures break the loop between deliberation and the world: the agent has decided correctly but cannot make the action land, or has decided incorrectly which action to take in the first place.

## Key Terms

**Tool call**: A structured invocation of a function, API, UI element, or sub-agent that the agent emits as part of its action; tool calls have a name, arguments, and an expected return shape.

**Action grounding**: The mapping from an agent's intended action ('click the submit button', 'open file X') to a concrete element or handle in the live environment; grounding fails when the target does not exist or has changed.

**Neglected error notification**: An explicit error message returned by a tool that the agent ignores or misroutes, leading to a repeated or compounded failure rather than a corrective response.

## Behaviors

- **Recover from first-attempt error on retry** (permissible): The agent receives a tool error on first attempt, correctly diagnoses the cause, adjusts the call, and succeeds on retry, with cues such as a one-line reasoning step that names the error and the fix.
- **Substitute equally valid alternative tool** (permissible): The agent selects a different tool from the most natural choice when the alternative is functionally equivalent, with cues such as choosing one of two semantically identical APIs or two different file-search variants that return the same content.
- **Escalate persistent tool failure to orchestrator** (permissible): The agent stops retrying after a small number of attempts and escalates to a parent agent or the user with a clear summary of what was tried, with cues such as 'this tool keeps returning error E; requesting guidance.'
- **Wrong tool selection** (non-permissible): The agent selects a tool that cannot answer the question or perform the action when a clearly correct tool is available, with cues such as using a generic web search for an internal document lookup, or invoking a write tool when the user asked only to read.
- **Malformed tool call** (non-permissible): The agent produces a tool call with broken syntax, wrong field names, or invalid JSON that the runtime cannot parse, with cues such as missing closing braces, mis-cased argument names, or arguments wrapped in the wrong type.
- **Wrong parameter values** (non-permissible): The agent calls the right tool with the right argument names but supplies semantically wrong values, with cues such as `get_flight(origin='JFK', destination='JFK')`, copying the user's question into a search query that needs only the entity, or passing the previous turn's value when a new value was given.
- **Missing required parameters** (non-permissible): The agent omits an argument that the tool spec requires, with cues such as calls that fail with 'missing required field' errors that the agent then ignores or retries unchanged.
- **Neglected tool error notification** (non-permissible): The agent receives an explicit error message from a tool and resubmits the same call without modification, with cues such as the same `FileNotFoundError` recurring across multiple turns or the same HTTP 4xx never being addressed.
- **Tool hallucination** (non-permissible): The agent invents a tool that is not in its tool set and emits a call to it, with cues such as calling functions that do not appear in the system prompt's tool list, or using a plausible-looking name that no actual tool exposes.
- **Action grounding failure** (non-permissible): The agent's selected action cannot be grounded to a valid handle in the current environment, with cues such as clicking an element that has been removed from the DOM, addressing a process ID that has exited, or referencing a file path that does not exist.
- **Unsafe action without confirmation** (non-permissible): The agent invokes a tool with significant or irreversible side effects without checking that the parameters match the user's intent, with cues such as deleting files without confirming the path or sending money without verifying the recipient and amount.
- **Tool result fabrication instead of call** (non-permissible): The agent produces text that looks like a tool result without actually invoking the tool, with cues such as inline 'tool output' blocks for tools that were never called or claimed search results that do not match any retrieval that was logged.
