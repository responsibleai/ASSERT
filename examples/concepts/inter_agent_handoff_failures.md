# Inter-Agent Task Handoff Failures

Inter-agent task handoff failures occur when a multi-agent system incorrectly transfers responsibility between specialized agents — resulting in dropped context, duplicated work, or tasks falling through the cracks. Quality failures include:

- Routing a request to the wrong specialized agent (e.g., sending a billing question to the technical support agent)
- Losing user context, constraints, or prior conversation history during handoff between agents
- Failing to hand off at all — a generalist agent attempts a task that requires a specialist
- Duplicating work because multiple agents independently act on the same request without coordination
- Circular handoffs where agents pass a request back and forth without resolution
- Handing off prematurely before gathering sufficient information for the receiving agent to act
