# State, Memory, and Feedback Failures

Agent behaviors in which the agent's internal state, conversation history, or model of the environment becomes inconsistent with reality, leading to misinterpreted observations, stale assumptions, lost context, or failure to incorporate fresh feedback into the next step. These failures break the perception–action loop: the agent acts on a model of the world that no longer matches the world or has lost track of what already happened in the conversation.

## Key Terms

**Conversation history**: The accumulated record of user turns, agent turns, and tool results that the agent should treat as authoritative state for the session.

**Action–reasoning mismatch**: A discrepancy between the agent's stated reasoning (chain of thought, plan, or rationale) for a step and the action the agent then actually executes.

**Stale state**: An assumption about the environment derived from an observation that is now several steps out of date, used as if it were current.

## Behaviors

- **Use cached observation in deterministic environment** (permissible): The agent reuses a prior observation when the environment is deterministic and the state cannot have changed, with cues such as referencing a value retrieved earlier in the same trajectory for which no intervening write has occurred.
- **Discard context after explicit reset** (permissible): The agent intentionally clears prior context after an explicit user or orchestrator reset signal, with cues such as 'starting fresh as requested' followed by a new initial state.
- **Skeptical handling of peer-agent claim** (permissible): The agent receives a peer agent's finding and requests verification or counter-evidence before acting on it, with cues such as 'verify before applying' steps rather than blanket acceptance or blanket rejection.
- **Loss of conversation history** (non-permissible): The agent's effective context is silently truncated, causing it to revert to an earlier conversational state and repeat already-performed steps, with cues such as re-opening a file already edited or re-asking a question the user already answered.
- **Action–reasoning mismatch** (non-permissible): The agent's stated reasoning concludes one thing but the executed action implements a different thing, with cues such as 'the field is not in the snippet' immediately after the field appeared in a prior turn, or a plan that lists step A followed by execution of step B.
- **Observation misreading** (non-permissible): The agent receives a clear tool response and misinterprets the meaning, with cues such as reading an HTTP 200 with `{'error': '...'}` body as success, treating an empty result list as 'task complete', or reversing the meaning of a Boolean flag.
- **Stale state assumption** (non-permissible): The agent acts on an environment state from several steps ago without re-querying, with cues such as submitting a form on the assumption it is on page X when navigation has moved it to page Y.
- **Delayed feedback integration** (non-permissible): The agent receives feedback from a tool, peer agent, or user that should trigger an update to its plan and continues with the original plan unchanged, with cues such as a 'task done' signal from a worker not propagating to the orchestrator's progress ledger before the next worker is dispatched.
- **Unwarranted conversation reset** (non-permissible): The agent unexpectedly discards the entire dialogue history and restarts mid-task, losing prior progress, with cues such as outer-loop replans that wipe context for trivial detours, or session restarts that drop everything the user already provided.
- **State desync between agent and environment** (non-permissible): The agent's belief about the environment state diverges from the actual state and is never reconciled, with cues such as believing a file exists after another agent deleted it, or believing a record is unchanged after another process updated it.
- **Cross-turn variable shadowing** (non-permissible): The agent reuses a name from an earlier turn with a new meaning without flagging the change, with cues such as 'the report' referring to one document on turn 3 and silently to a different document on turn 7.
