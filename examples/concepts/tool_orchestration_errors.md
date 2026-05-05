# Tool Orchestration Errors

Tool orchestration errors occur when a multi-agent system mismanages the sequence, selection, or composition of tool calls across agents — leading to incorrect results, wasted computation, or incomplete task execution. Quality failures include:

- Calling tools in the wrong order (e.g., booking a flight before checking availability or budget)
- Skipping a required tool call that a downstream agent depends on (e.g., not validating inputs before passing to an execution agent)
- Calling the same tool redundantly across multiple agents without deduplication
- Using tool results from one agent's context in another agent without verifying they are still valid or current
- Failing to handle tool errors or timeouts gracefully — one agent's tool failure silently breaks the entire pipeline
- Fabricating tool results instead of calling the actual tool (hallucinating outputs that look like tool responses)
