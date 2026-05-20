"""Same agent, wrapped by the AgentShield runtime defined in guardrails.yaml.

Drop-in callable replacement: same `chat(message: str) -> str` signature,
so swap target.callable in eval_config.yaml from
`examples.incident_triage_simple.agent:chat` to this module's `chat` to
re-run the eval against the guarded build.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from agent_shield import RuntimeBuilder

from examples.incident_triage_simple.agent import (
    AGENT_MODEL,
    MAX_STEPS,
    SYSTEM_PROMPT,
    TOOL_SCHEMAS,
    _call_parts,
    _msg_dict,
    _tools,
    _tracer,
)

import litellm

_RUNTIME = RuntimeBuilder.from_yaml(str(Path(__file__).with_name("guardrails.yaml"))).build()


def chat(message: str) -> str:
    state: dict[str, Any] = {}
    registry = _tools(state)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": message},
    ]
    session = _RUNTIME.new_session(
        session_id=f"its-{uuid.uuid4().hex[:12]}",
        correlation_id=f"chat-{uuid.uuid4().hex[:8]}",
    )
    session.begin_turn()

    try:
        with _tracer.start_as_current_span("agent.chat") as span:
            span.set_attribute("openinference.span.kind", "AGENT")
            span.set_attribute("agentshield.guarded", True)
            for _ in range(MAX_STEPS):
                resp = litellm.completion(
                    model=AGENT_MODEL, messages=messages, tools=TOOL_SCHEMAS, tool_choice="auto",
                )
                am = resp.choices[0].message
                tool_calls = getattr(am, "tool_calls", None)
                if not tool_calls:
                    final = str(getattr(am, "content", "") or "")
                    span.set_attribute("output.value", final)
                    return final
                messages.append(_msg_dict(am))
                for tc in tool_calls:
                    call_id, name, args = _call_parts(tc)
                    fn = registry.get(name)
                    if fn is None:
                        result: Any = {"error": "unknown_tool", "tool_name": name}
                    else:
                        pre = session.validate_tool_call(name, args)
                        if not pre.verdict.allowed:
                            result = {"error": "blocked_by_guardrail", "stage": "pre", "reason": pre.verdict.reason}
                        else:
                            raw = fn(**(pre.params or args))
                            post = session.validate_tool_output(name, json.dumps(raw, default=str))
                            if not post.verdict.allowed:
                                result = {"error": "blocked_by_guardrail", "stage": "post", "reason": post.verdict.reason}
                            else:
                                session.record_tool_success(name, json.dumps(raw, default=str))
                                result = raw
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "name": name,
                        "content": json.dumps(result, ensure_ascii=False, default=str),
                    })
            return "[agent: step budget exhausted]"
    finally:
        try:
            session.end_turn()
        except Exception:
            pass


if __name__ == "__main__":
    print(chat("Please triage alert ALR-002."))
