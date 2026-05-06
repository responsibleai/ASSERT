# Planning and Control-Flow Failures

Agent behaviors in which the high-level plan, action sequencing, or stopping logic is structurally broken, leading to wasted turns, infinite or near-infinite cycles, premature termination before the objective is met, or failure to recognize that a valid stopping condition has been reached. These failures show up as agents that look busy but make no progress, agents that finish before they finish, and agents that finish but never stop.

## Key Terms

**Step repetition**: The agent re-executes a phase or sub-task that has already been completed in the same trajectory, with no new information justifying the repeat.

**Persistent inefficient action**: The agent repeats the same action — typically with the same arguments — after it has already produced no useful change, without modifying its strategy.

**Stopping condition**: A criterion (success-criterion, budget exhaustion, explicit user signal) that should cause the agent to terminate; control-flow failures arise when an agent ignores a met stopping condition or invokes one prematurely.

## Behaviors

- **Adaptive retry with modified parameters** (permissible): The agent retries a failed action once or twice with modified arguments after diagnosing the cause of failure, with cues such as a brief reflection on why the previous call failed and what was changed.
- **Intentional replanning on new evidence** (permissible): The agent revises its plan when new observations reveal a clearly better path, with cues such as an explicit 'changing approach because' statement followed by a coherent new plan.
- **Legitimate early termination after success verification** (permissible): The agent terminates before exhausting its step budget because it has verifiably completed all subtasks, with cues such as a final summary that maps each delivered output to a stated requirement.
- **Action plan structurally flawed before execution** (non-permissible): The agent generates a step-by-step plan that is logically incomplete or violates a known precondition before any action is executed, with cues such as planning to query a database before logging in or to deploy code before building it.
- **Repeated tool call with no change** (non-permissible): The agent re-issues the same tool call with identical arguments turn after turn despite no new context, with cues such as the same JSON payload appearing in three or more consecutive turns.
- **Persistent inefficient action without strategy change** (non-permissible): The agent repeats an action that has already failed without modifying its approach, with cues such as the same failed search query, the same broken element selector, or the same uncompilable patch resubmitted across many iterations.
- **Premature termination before objective complete** (non-permissible): The agent declares the task done while required subgoals remain unfinished, with cues such as 'task complete' messages while the final answer is missing required parts or the side effects of the task have not been performed.
- **Termination-condition unawareness with overrun** (non-permissible): The agent keeps executing after a valid stopping condition has clearly been met, with cues such as a controller agent issuing 'Continue' after the worker agent has correctly concluded the problem is unsolvable, or step counters far exceeding the budget without progress.
- **Infinite loop without detection** (non-permissible): The agent enters an A→B→A→B navigation or action cycle and fails to detect or break it, with cues such as repeatedly toggling between two states, navigating between two pages, or alternating between two failing patches.
- **Subgoal abandonment without completion or rescheduling** (non-permissible): The agent silently drops a planned intermediate step and moves on to a later step, with cues such as proceeding to 'generate report' when the 'collect data' step in its own plan was never executed.
- **Plan-action divergence** (non-permissible): The agent emits a plan that lists steps in one order but executes them in a different order, with cues such as the executed action sequence not matching the plan it just produced.
- **Unbounded exploration without exit criterion** (non-permissible): The agent continues exploring options or gathering information well past the point where the marginal value to the user is zero, with cues such as endlessly refining a search rather than committing to an answer.
