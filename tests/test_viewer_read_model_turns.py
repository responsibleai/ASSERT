"""Unit tests for `_materialize_target_messages` turn semantics.

Only the auditor (user) and the target (assistant) emit "turns".
A target turn = one block of consecutive assistant emissions.
System messages, tool messages, and tool-call edits never get a
`judgeTurn` label.
"""

from __future__ import annotations

import unittest

from p2m.viewer_read_model import (
    _count_target_conversation_messages,
    _materialize_target_messages,
)


def _msg(role: str, content: str = "...", *, raw: dict | None = None) -> dict:
    event = {
        "view": ["target"],
        "actor": "auditor" if role in {"system", "user"} else "target",
        "edit": {
            "type": "set_system_message" if role == "system" else "add_message",
            "message": {"role": role, "content": content},
        },
    }
    if raw is not None:
        event["raw"] = raw
    return event


def _tool_call(name: str = "search", args: dict | None = None) -> dict:
    return {
        "view": ["target"],
        "actor": "tool",
        "edit": {
            "type": "tool_call",
            "tool_name": name,
            "tool_args": args or {},
            "tool_result": "",
        },
    }


class MaterializeTargetMessagesTurnSemanticsTest(unittest.TestCase):
    def test_simple_single_agent_chat(self) -> None:
        transcript = {
            "events": [
                _msg("system", "sys"),
                _msg("user", "hi"),
                _msg("assistant", "hello"),
                _msg("user", "bye"),
                _msg("assistant", "ok"),
            ]
        }
        messages = _materialize_target_messages(transcript)
        roles = [(m["role"], m["judgeTurn"]) for m in messages]
        self.assertEqual(
            roles,
            [
                ("system", None),
                ("user", 1),
                ("assistant", 2),
                ("user", 3),
                ("assistant", 4),
            ],
        )
        self.assertEqual(_count_target_conversation_messages(transcript), 4)

    def test_consecutive_assistants_collapse_into_one_turn(self) -> None:
        # Multi-agent target: intent_classifier hands off to flight_searcher
        # then to a final assistant — all between two auditor messages.
        transcript = {
            "events": [
                _msg("system", "sys"),
                _msg("user", "plan a trip"),
                _msg("assistant", "classifying...", raw={"_node": "intent_classifier"}),
                _msg("assistant", "searching...", raw={"_node": "flight_searcher"}),
                _msg("assistant", "here you go", raw={"_node": "final"}),
                _msg("user", "thanks"),
                _msg("assistant", "you're welcome", raw={"_node": "final"}),
            ]
        }
        messages = _materialize_target_messages(transcript)
        turns = [m["judgeTurn"] for m in messages]
        self.assertEqual(turns, [None, 1, 2, 2, 2, 3, 4])
        self.assertEqual(_count_target_conversation_messages(transcript), 4)

    def test_tool_calls_and_tool_messages_inherit_assistant_turn(self) -> None:
        # tool_call edits and tool-role messages between two assistants must
        # NOT split the assistant chain, and they SHOULD inherit the
        # surrounding assistant turn label so the viewer can group them
        # under the right turn.
        transcript = {
            "events": [
                _msg("user", "hi"),
                _msg("assistant", "thinking", raw={"_node": "agent_a"}),
                _tool_call("search_flights", {"q": "SFO"}),
                _msg("tool", "tool result here"),
                _msg("assistant", "answer", raw={"_node": "agent_b"}),
            ]
        }
        messages = _materialize_target_messages(transcript)
        turns = [(m["role"], m["judgeTurn"]) for m in messages]
        self.assertEqual(
            turns,
            [
                ("user", 1),
                ("assistant", 2),
                ("tool", 2),
                ("tool", 2),
                ("assistant", 2),
            ],
        )
        self.assertEqual(_count_target_conversation_messages(transcript), 2)

    def test_tools_before_assistant_text_inherit_upcoming_target_turn(self) -> None:
        # Regression test for the screenshot bug: tool calls/results that
        # arrive immediately after an auditor user message — but BEFORE
        # the target's assistant text — must be labeled with the target's
        # upcoming turn (auditor=11 -> tools=12, assistant=12), not the
        # auditor's just-finished turn (would have been 11 across the board).
        transcript = {
            "events": [
                _msg("user", "what is the cheapest hotel?"),
                _tool_call("search_flights", {"to": "YHZ"}),
                _tool_call("search_hotels", {"city": "Halifax"}),
                _msg("tool", "search_flights result"),
                _msg("tool", "search_hotels result"),
                _msg("assistant", "Here is the cheapest option..."),
            ]
        }
        messages = _materialize_target_messages(transcript)
        turns = [(m["role"], m["type"], m["judgeTurn"]) for m in messages]
        self.assertEqual(
            turns,
            [
                ("user", "message", 1),
                ("tool", "tool_call", 2),
                ("tool", "tool_call", 2),
                ("tool", "message", 2),
                ("tool", "message", 2),
                ("assistant", "message", 2),
            ],
        )
        self.assertEqual(_count_target_conversation_messages(transcript), 2)

    def test_agent_field_propagates_from_raw_node(self) -> None:
        transcript = {
            "events": [
                _msg("user", "hi"),
                _msg("assistant", "a", raw={"_node": "intent_classifier"}),
                _msg("assistant", "b", raw={"_node": "flight_searcher"}),
                _msg("assistant", "c", raw={}),
                _msg("assistant", "d", raw=None),
            ]
        }
        messages = _materialize_target_messages(transcript)
        agents = [m.get("agent") for m in messages]
        self.assertEqual(agents, [None, "intent_classifier", "flight_searcher", None, None])

    def test_agent_field_is_not_set_on_user_or_tool_messages(self) -> None:
        transcript = {
            "events": [
                _msg("user", "hi", raw={"_node": "should_be_ignored_for_user"}),
                _tool_call("search"),
                _msg("tool", "tool", raw={"_node": "ignored"}),
                _msg("assistant", "a", raw={"_node": "agent_a"}),
            ]
        }
        messages = _materialize_target_messages(transcript)
        agents = [(m["role"], m.get("agent")) for m in messages]
        self.assertEqual(
            agents,
            [
                ("user", None),
                ("tool", None),
                ("tool", None),
                ("assistant", "agent_a"),
            ],
        )

    def test_count_returns_distinct_turn_count_not_message_count(self) -> None:
        # 1 user + 3 assistants (collapsed) + 4 tool messages = 5 messages,
        # but only 2 turns.
        transcript = {
            "events": [
                _msg("user", "go"),
                _msg("assistant", "a"),
                _tool_call("t1"),
                _tool_call("t2"),
                _msg("assistant", "b"),
                _tool_call("t3"),
                _msg("tool", "tool result"),
                _msg("assistant", "c"),
            ]
        }
        self.assertEqual(_count_target_conversation_messages(transcript), 2)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
