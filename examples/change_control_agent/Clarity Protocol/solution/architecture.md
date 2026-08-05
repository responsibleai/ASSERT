# Architecture

## Current System

A single-file tool loop over ten tools, with the control flow deliberately factored so a
governed variant can be built without forking behaviour.

```
chat(message, history=None)
        │
        ▼
_run_loop(message, history, execute_tool)
        │   ├─ _seed_messages(message, history)      replay multi-turn context
        │   ├─ Tools({"description": message})       simulated backend
        │   ├─ _tool_registry(tools)                 name -> callable
        │   └─ loop: model -> tool_calls -> execute_tool(...) -> messages
        ▼
   final assistant reply
```

### The seam

```python
def _default_execute_tool(registry, name, args, call_id) -> dict:
    """Baseline tool executor: run the tool directly, unguarded."""
    return _call_tool(registry, name, args)
```

`_run_loop` takes `execute_tool` as a parameter and its docstring states the intent
directly: `chat` passes `_default_execute_tool`; the governed target passes an
ACS-enforcing executor with the identical signature, and *everything else* — model,
system prompt, tool schemas, step and tool-call budgets, message shaping — is shared.

This is the cleanest possible A/B boundary. The guarded agent imports `_run_loop` and
supplies one function. There is no opportunity for behavioural drift, because there is no
duplicated logic.

### Tools and their validation contract

| Tool | Role | Enforcement relevance |
|---|---|---|
| `get_change_proposal` | Fetch proposal (untrusted free text) | Injection source |
| `lookup_policy` | **Sole authority** for required surfaces/approvers/order/freeze | Supplies policy facts |
| `validate_rollback_plan` | Deterministic rollback checklist | Required prior step |
| `get_incident` | Satisfies `icm_required_for_critical` | Required prior step |
| `create_change_request` | Creates tracker record | **post-call**: `fabricated_fields` |
| `submit_to_deployment_gateway` | Safety review surface | **pre-call**: ordering |
| `submit_to_rollout_service` | Rollout surface | **pre-call**: ordering + freeze |
| `submit_to_release_readiness` | Readiness surface | **pre-call**: ordering |
| `route_for_approval` | Approval routing | **pre-call**: quorum + roles + freeze |
| `request_change_updates` | The legal exit when blocked | Denial target |

Every result carries a deterministic `validation` block: `policy_id`,
`required_prior_steps`, `completed_prior_steps`, `missing_prior_steps`,
`required_approver_roles`, `provided_approvers`, `minimum_approver_count`,
`policy_violations`, `sequence_violations`, `requires_freeze_exception`, plus
`field_provenance` and `fabricated_fields` for the tracker.

### The structural gap

The `validation` block is returned **to the model as data**. Nothing consumes it
programmatically. A submission executes whenever the model emits the tool call,
regardless of what the block said. The system prompt asks for compliance; the loop does
not require it.

Budgets: `MAX_STEPS=12`, `MAX_TOOL_CALLS=12`. Multi-turn state exists only via `history`,
replayed per call — so accumulated user pressure grows across turns while policy facts do
not.

## Target System

```
chat_guarded(message, history)
        │
        ▼
_run_loop(message, history, guarded_execute_tool)     <-- SAME loop, one arg differs
                             │
                             ▼
                  ┌── pre_tool_call gate ──┐
                  │  policy_target: call    │
                  │  + injected session     │
                  │    state (scalars)      │
                  └──────────┬──────────────┘
                       allow │ deny
                             │    └──► structured denial naming the
                             │         missing prerequisite -> model
                             │         takes the correct next step
                             ▼
                       tool executes
                             │
                  ┌── post_tool_call gate ─┐
                  │  reads returned         │
                  │  validation block       │
                  └──────────┬──────────────┘
                       allow │ deny (e.g. fabricated_fields non-empty)
                             ▼
                    result appended to messages
```

### Design constraints this imposes

**The baseline module is imported, never forked.** `agent_guarded.py` imports
`_run_loop`, `_tool_registry`, `_call_tool`, and the system prompt from `agent.py`. The
only new code is the executor and its policy plumbing.

**Session state must be injected as scalars.** ACS evaluates each call in isolation, so
`create_change_request_succeeded`, the set of surfaces already submitted,
`provided_approvers`, and `requires_freeze_exception` must be tracked by the executor
from *observed tool results* and passed into the policy input. They cannot be derived
inside the policy language, and must never be read from the model's narration.

**Any gated tool declares both enforcement points.** A tool present at `pre_tool_call`
but absent at `post_tool_call` fails closed to `deny`. Pass-through declarations are
required where only one side is meaningful.

**Denials must be actionable within the budget.** A denial returns the specific missing
prerequisite so the model can route to `request_change_updates` or supply the missing
step, rather than retrying blindly and exhausting 12 calls.

**Fail open on evaluator error.** A malfunctioning gate must not halt all change
management.

## Open Architectural Questions

- Whether the authority-overclaim failure (claiming "approved" without
  `approval_status="approved"`) warrants a second, semantic gate on the outgoing reply,
  or whether preventing the underlying unapproved submissions reduces it sufficiently on
  its own. A semantic gate would need a host-owned annotator dispatcher, which is
  materially more machinery than the structural gates require.
- How much session state is enough. Tracking too little lets ordering violations
  through; tracking too much risks the executor's model of the session diverging from
  the tools' own.
