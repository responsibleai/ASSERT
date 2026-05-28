"""Tests for the JSON action parser in ``p2m init``."""

from __future__ import annotations

import json
import unittest

from p2m.init._design_agent import ParsedAction, ParseError, _parse_action, _strip_fences


class StripFencesTest(unittest.TestCase):
    def test_standard_json_fence(self) -> None:
        text = '```json\n{"a": 1}\n```'
        self.assertEqual(_strip_fences(text), '{"a": 1}')

    def test_fence_no_language_tag(self) -> None:
        text = '```\n{"a": 1}\n```'
        self.assertEqual(_strip_fences(text), '{"a": 1}')

    def test_double_fencing(self) -> None:
        text = '```json\n```json\n{"a": 1}\n```\n```'
        self.assertEqual(_strip_fences(text), '{"a": 1}')

    def test_trailing_whitespace(self) -> None:
        text = '```json\n{"a": 1}\n```   \n'
        self.assertEqual(_strip_fences(text), '{"a": 1}')

    def test_no_fence_passthrough(self) -> None:
        text = '{"a": 1}'
        self.assertEqual(_strip_fences(text), '{"a": 1}')


class ParseActionTest(unittest.TestCase):
    def _action_json(self, action: str, content: str, yaml_str: str | None = None) -> str:
        d: dict = {"action": action, "content": content}
        if yaml_str is not None:
            d["yaml"] = yaml_str
        return json.dumps(d)

    def test_ask_action(self) -> None:
        raw = self._action_json("ask", "What system?")
        result = _parse_action(raw)
        self.assertIsInstance(result, ParsedAction)
        assert isinstance(result, ParsedAction)
        self.assertEqual(result.action, "ask")
        self.assertEqual(result.content, "What system?")
        self.assertIsNone(result.yaml_str)

    def test_propose_action(self) -> None:
        raw = self._action_json("propose", "Here you go", yaml_str="suite: test\n")
        result = _parse_action(raw)
        self.assertIsInstance(result, ParsedAction)
        assert isinstance(result, ParsedAction)
        self.assertEqual(result.action, "propose")
        self.assertEqual(result.yaml_str, "suite: test\n")

    def test_done_action(self) -> None:
        raw = self._action_json("done", "Final config", yaml_str="suite: final\n")
        result = _parse_action(raw)
        self.assertIsInstance(result, ParsedAction)
        assert isinstance(result, ParsedAction)
        self.assertEqual(result.action, "done")

    def test_done_without_yaml_is_error(self) -> None:
        raw = self._action_json("done", "No yaml here")
        result = _parse_action(raw)
        self.assertIsInstance(result, ParseError)

    def test_propose_without_yaml_is_error(self) -> None:
        raw = self._action_json("propose", "No yaml here")
        result = _parse_action(raw)
        self.assertIsInstance(result, ParseError)

    def test_invalid_action_is_error(self) -> None:
        raw = json.dumps({"action": "think", "content": "hmm"})
        result = _parse_action(raw)
        self.assertIsInstance(result, ParseError)
        assert isinstance(result, ParseError)
        self.assertIn("think", result.reason)

    def test_missing_content_is_error(self) -> None:
        raw = json.dumps({"action": "ask"})
        result = _parse_action(raw)
        self.assertIsInstance(result, ParseError)

    def test_non_json_input_is_error(self) -> None:
        result = _parse_action("This is not JSON at all")
        self.assertIsInstance(result, ParseError)

    def test_fenced_json_is_parsed(self) -> None:
        inner = self._action_json("ask", "Question?")
        raw = f"```json\n{inner}\n```"
        result = _parse_action(raw)
        self.assertIsInstance(result, ParsedAction)
        assert isinstance(result, ParsedAction)
        self.assertEqual(result.action, "ask")

    def test_non_object_json_is_error(self) -> None:
        result = _parse_action("[1, 2, 3]")
        self.assertIsInstance(result, ParseError)


if __name__ == "__main__":
    unittest.main()
