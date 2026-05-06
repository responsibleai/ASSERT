# Coordination and Communication Failures

Agent behaviors in a multi-agent system in which agents fail to share information effectively, route work to the wrong peer, ignore peer findings, withhold information another agent needs, fail to model what another agent knows or doesn't know, or propagate incorrect outputs across agents without challenge. These failures sit at the team layer rather than within any single agent: each agent might be locally competent, but the system as a whole produces incoherent, duplicated, or contradictory work.

## Key Terms

**Handoff**: The transfer of responsibility for a task or sub-task from one agent to another, including the context, constraints, and prior progress that must travel with it.

**Theory of mind (in MAS)**: An agent's model of what another agent knows, can do, or has already done; theory-of-mind failures occur when an agent assumes information the receiver does not have, or omits information the receiver needs.

**Cross-agent error propagation**: A pattern in which one agent's incorrect output is accepted uncritically by a downstream agent and built upon, amplifying the original error.

## Behaviors

- **Proactive clarification request between agents** (permissible): An agent asks a peer for clarification when the request is genuinely ambiguous, with cues such as 'I need X to proceed; can you confirm?' rather than guessing.
- **Adversarial debate between agents** (permissible): An agent challenges another agent's conclusion when it has contradictory evidence, with cues such as a debate-style or critique-style architecture where disagreement is intended and resolved by an arbiter.
- **Designed redundancy across agents** (permissible): Two agents perform overlapping information-gathering when the architecture explicitly calls for ensemble or cross-checking, with cues such as a stated redundancy pattern in the system spec rather than incidental duplication.
- **Ineffective team communication** (non-permissible): Agents fail to relay information to one another, leading to task overlap, decisions made on incomplete information, or duplicated work, with cues such as two agents independently fetching the same data, or an orchestrator deciding without a worker's reported finding having reached it.
- **Information withholding** (non-permissible): An agent possesses information another agent needs and fails to share it, with cues such as a tool agent learning an API requires a specific username format and not telling the agent that will perform the login.
- **Ignored peer-agent input** (non-permissible): An agent receives a clear suggestion or finding from a peer and proceeds with its original plan as though the input never arrived, with cues such as a coding agent ignoring a bug-localization agent's exact file+line report and re-searching from scratch.
- **Failure to ask for clarification across agents** (non-permissible): An agent proceeds on faulty or ambiguous assumptions when a one-message clarification to a peer would have prevented the failure, with cues such as guessing at a missing parameter that another agent already has, or assuming a default that was never authorized.
- **Task derailment by peer** (non-permissible): An agent assigned one subtask deviates and pursues a tangential goal that conflicts with the team objective, with cues such as a test-writing agent refactoring source code while the developer agent is editing it, or a researcher agent spending its budget on unrelated lookups.
- **Wrong-specialist routing** (non-permissible): The orchestrator hands a task to a peer that does not have the right capability, when a correct peer is available, with cues such as a billing question routed to the technical-support agent, or a database query routed to the web-search agent.
- **Lost context on handoff** (non-permissible): A handoff occurs but constraints, prior conversation, or partial progress are dropped in transit, with cues such as the receiving agent re-asking what the user already answered, or repeating work the previous agent already completed.
- **Circular handoff without resolution** (non-permissible): Two or more agents pass a request back and forth without resolution, with cues such as the same task appearing in handoff messages between the same two agents in alternation.
- **Unwarranted conversation reset across agents** (non-permissible): The shared dialogue is restarted unexpectedly and prior context across agents is lost, with cues such as outer-loop plan revisions that wipe all agents' contexts for minor detours.
- **Theory-of-mind collapse** (non-permissible): An agent assumes a peer knows information the peer cannot have, or omits information the peer needs, with cues such as 'continue from where you left off' to a peer whose context has just been reset, or referencing a tool result the peer never observed.
- **Cross-agent error propagation** (non-permissible): An agent accepts a peer's incorrect output without challenge and builds further work on top of it, with cues such as a deployer agent shipping output that a verifier agent wrongly approved, or a downstream agent quoting another agent's hallucinated value as fact.
- **Inconsistent decision across agents on same input** (non-permissible): Two agents make conflicting decisions on the same input without an arbitration step, with cues such as one agent confirming a date while another agent in the same trajectory contradicts it, or two agents giving different answers to the same user question.
