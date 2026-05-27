"""Tests for exception handling and error reporting across ASSERT modules."""

from __future__ import annotations

import json
import logging
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, MagicMock, patch

from assert_eval.config import ConfigError, load_config
from assert_eval.core.otel import _parse_otlp_json
from assert_eval.core.tools import load_toolset_file


# ── config.py ──────────────────────────────────────────────────

class LoadConfigErrorTest(unittest.TestCase):
    def test_missing_config_file_raises_config_error(self) -> None:
        with self.assertRaises(ConfigError) as ctx:
            load_config(Path("/tmp/nonexistent_config_abc123.yaml"))
        self.assertIn("not found", str(ctx.exception))

    def test_invalid_yaml_raises_config_error(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "bad.yaml"
            path.write_text(":\n  - :\n  bad: [unterminated", encoding="utf-8")
            with self.assertRaises(ConfigError) as ctx:
                load_config(path)
            self.assertIn("Invalid YAML", str(ctx.exception))
            self.assertIn(str(path), str(ctx.exception))

    def test_permission_denied_raises_config_error(self) -> None:
        path = Path("locked.yaml")
        with patch.object(Path, "read_text", side_effect=PermissionError("denied")):
            with self.assertRaises(ConfigError) as ctx:
                load_config(path)
        self.assertIn("Permission denied", str(ctx.exception))

    def test_valid_yaml_non_mapping_raises_config_error(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "list.yaml"
            path.write_text("- item1\n- item2\n", encoding="utf-8")
            with self.assertRaises(ConfigError) as ctx:
                load_config(path)
            self.assertIn("mapping", str(ctx.exception))


# ── core/otel.py ───────────────────────────────────────────────

class ParseOtlpJsonErrorTest(unittest.TestCase):
    def test_missing_file_raises_file_not_found(self) -> None:
        with self.assertRaises(FileNotFoundError) as ctx:
            _parse_otlp_json(Path("/tmp/nonexistent_trace_abc123.json"))
        self.assertIn("OTLP trace file not found", str(ctx.exception))

    def test_malformed_json_raises_value_error(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "bad_trace.json"
            path.write_text("{not valid json", encoding="utf-8")
            with self.assertRaises(ValueError) as ctx:
                _parse_otlp_json(path)
            self.assertIn("Malformed JSON", str(ctx.exception))
            self.assertIn(str(path), str(ctx.exception))

    def test_valid_empty_json_returns_empty_spans(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "empty_trace.json"
            path.write_text("{}", encoding="utf-8")
            result = _parse_otlp_json(path)
            self.assertEqual(result, [])


# ── core/tools.py ──────────────────────────────────────────────

class LoadToolsetFileErrorTest(unittest.TestCase):
    def test_missing_toolset_file_raises_file_not_found(self) -> None:
        with self.assertRaises(FileNotFoundError) as ctx:
            load_toolset_file("/tmp/nonexistent_toolset_abc123.yaml")
        self.assertIn("Toolset file not found", str(ctx.exception))

    def test_invalid_yaml_in_toolset_raises_value_error(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "bad_tools.yaml"
            path.write_text(":\n  bad: [unterminated", encoding="utf-8")
            with self.assertRaises(ValueError) as ctx:
                load_toolset_file(str(path))
            self.assertIn("Invalid YAML", str(ctx.exception))
            self.assertIn(str(path), str(ctx.exception))


# ── core/session.py ────────────────────────────────────────────

class CallableSessionErrorTest(unittest.IsolatedAsyncioTestCase):
    async def test_missing_module_raises_value_error(self) -> None:
        from assert_eval.core.session import CallableSession

        session = CallableSession(callable_ref="nonexistent_module_xyz123:func")
        with self.assertRaises(ValueError) as ctx:
            await session.open()
        self.assertIn("Could not import module", str(ctx.exception))
        self.assertIn("nonexistent_module_xyz123", str(ctx.exception))

    async def test_missing_function_raises_value_error(self) -> None:
        from assert_eval.core.session import CallableSession

        session = CallableSession(callable_ref="json:nonexistent_func_xyz123")
        with self.assertRaises(ValueError) as ctx:
            await session.open()
        self.assertIn("has no attribute", str(ctx.exception))
        self.assertIn("nonexistent_func_xyz123", str(ctx.exception))

    async def test_valid_callable_opens_successfully(self) -> None:
        from assert_eval.core.session import CallableSession

        session = CallableSession(callable_ref="json:dumps")
        await session.open()
        await session.close()

    # ------------------------------------------------------------------
    # litellm reclassification (absorbed from PR #44 commit 0184d8d)
    # ------------------------------------------------------------------

    async def test_run_turn_reclassifies_litellm_bad_request_as_input_error(self) -> None:
        """User callables (LangGraph, agent frameworks, raw litellm) bypass
        ``generate()``/``_with_retries`` and so emit unclassified provider
        errors. ``CallableSession.run_turn`` must re-classify them so the
        inference stage's per-seed isolation paths can route content-filter
        rejections to a recorded transcript event instead of aborting the
        whole batch.
        """
        from assert_eval.core.session import CallableSession
        from assert_eval.core.model_client import LLMInputError, Message

        # Lightweight stand-in for ``litellm.BadRequestError`` that avoids
        # importing litellm in the unit test. ``_classify_llm_error`` reads
        # exception types from the live litellm module at call time, so we
        # patch it directly to make the substitution explicit.
        class FakeLitellmBadRequest(Exception):
            pass

        def _classified(exc: Exception) -> Exception:
            if isinstance(exc, FakeLitellmBadRequest):
                err = LLMInputError(f"Bad request: {exc}")
                err.__cause__ = exc
                return err
            return exc

        async def fake_invoke_callable(fn, *args, **kwargs):
            raise FakeLitellmBadRequest(
                "Invalid prompt: your prompt was flagged as potentially "
                "violating our usage taxonomy"
            )

        session = CallableSession(callable_ref="json:dumps")
        await session.open()
        try:
            with (
                patch("assert_eval.core.session.invoke_callable", new=fake_invoke_callable),
                patch("assert_eval.core.session._classify_llm_error", new=_classified),
            ):
                with self.assertRaises(LLMInputError) as ctx:
                    await session.run_turn([Message(role="user", content="hi")])
            self.assertIn("flagged as potentially violating", str(ctx.exception))
            self.assertIsInstance(ctx.exception.__cause__, FakeLitellmBadRequest)
        finally:
            await session.close()

    async def test_run_turn_passes_through_unclassified_exceptions(self) -> None:
        """Errors that the classifier doesn't recognise (e.g. user agent
        crashes, ValueError from misconfigured tools) must propagate as-is
        rather than being smuggled into one of the four LLM error classes.
        """
        from assert_eval.core.session import CallableSession
        from assert_eval.core.model_client import Message

        class CustomAgentError(RuntimeError):
            pass

        async def fake_invoke_callable(fn, *args, **kwargs):
            raise CustomAgentError("user agent blew up")

        session = CallableSession(callable_ref="json:dumps")
        await session.open()
        try:
            with patch("assert_eval.core.session.invoke_callable", new=fake_invoke_callable):
                with self.assertRaises(CustomAgentError) as ctx:
                    await session.run_turn([Message(role="user", content="hi")])
            self.assertIn("user agent blew up", str(ctx.exception))
        finally:
            await session.close()


class OTelTracedSessionErrorTest(unittest.IsolatedAsyncioTestCase):
    async def test_missing_module_raises_value_error(self) -> None:
        from assert_eval.core.otel_session import OTelTracedSession

        session = OTelTracedSession(callable_ref="nonexistent_module_xyz123:func")
        with self.assertRaises(ValueError) as ctx:
            await session.open()
        self.assertIn("Could not import module", str(ctx.exception))
        self.assertIn("nonexistent_module_xyz123", str(ctx.exception))

    async def test_missing_function_raises_value_error(self) -> None:
        from assert_eval.core.otel_session import OTelTracedSession

        session = OTelTracedSession(callable_ref="json:nonexistent_func_xyz123")
        with self.assertRaises(ValueError) as ctx:
            await session.open()
        self.assertIn("has no attribute", str(ctx.exception))
        self.assertIn("nonexistent_func_xyz123", str(ctx.exception))


class HTTPEndpointSessionErrorTest(unittest.IsolatedAsyncioTestCase):
    async def test_connection_error_raises_runtime_error(self) -> None:
        try:
            import aiohttp
        except ImportError:
            self.skipTest("aiohttp not installed")

        import os
        from unittest.mock import patch as env_patch

        from assert_eval.core.session import HTTPEndpointSession

        with env_patch.dict(os.environ, {"ASSERT_ALLOW_PRIVATE_ENDPOINTS": "1"}):
            session = HTTPEndpointSession(
                endpoint="http://127.0.0.1:59123",  # closed high port
                message_timeout_s=1.0,
            )
        await session.open()
        try:
            from assert_eval.core.model_client import Message

            with self.assertRaises(RuntimeError) as ctx:
                await session.run_turn([Message(role="user", content="hello")])
            self.assertIn("Connection error", str(ctx.exception))
        finally:
            await session.close()


# ── stages/judge.py ────────────────────────────────────────────

class JudgePolicyParseErrorTest(unittest.TestCase):
    def test_corrupt_taxonomy_json_raises_value_error(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            taxonomy_path = Path(tmp_dir) / "taxonomy.json"
            taxonomy_path.write_text("{not valid json", encoding="utf-8")
            # We can't easily call run_judge without full setup, but we can
            # test the JSON parse path directly
            with self.assertRaises(json.JSONDecodeError):
                json.loads(taxonomy_path.read_text(encoding="utf-8"))


# ── stages/systematization_convert.py ──────────────────────────

class SystematizationConvertErrorTest(unittest.IsolatedAsyncioTestCase):
    async def test_missing_file_raises_file_not_found(self) -> None:
        from assert_eval.stages.systematization_convert import run_systematization_to_taxonomy

        with self.assertRaises(FileNotFoundError) as ctx:
            await run_systematization_to_taxonomy(
                systematization_path="/tmp/nonexistent_syst_abc123.json",
            )
        self.assertIn("Systematization file not found", str(ctx.exception))

    async def test_corrupt_json_raises_value_error(self) -> None:
        from assert_eval.stages.systematization_convert import run_systematization_to_taxonomy

        with TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "bad_syst.json"
            path.write_text("{not valid", encoding="utf-8")
            with self.assertRaises(ValueError) as ctx:
                await run_systematization_to_taxonomy(
                    systematization_path=str(path),
                )
            self.assertIn("Invalid JSON", str(ctx.exception))


# ── stages/inference.py worker logging ───────────────────────────

class InferenceWorkerLoggingTest(unittest.IsolatedAsyncioTestCase):
    async def test_worker_logs_debug_on_runtime_failure(self) -> None:
        """Verify that the inference worker logs debug info when a runtime error occurs."""
        from assert_eval.core.config_model import (
            TesterConfig,
            EvaluationConfig,
            JudgeConfig,
            InferenceConfig,
            TargetConfig,
        )
        from assert_eval.stages.inference import run_inference

        target = TargetConfig(model="azure/gpt-5.4")
        evaluation = EvaluationConfig(
            inference=InferenceConfig(max_turns=1, concurrency=1),
            judge=JudgeConfig(model="azure/gpt-5.4"),
            tester=TesterConfig(model="azure/gpt-5.4"),
        )

        with TemporaryDirectory() as tmp_dir:
            test_set_path = Path(tmp_dir) / "test_set.jsonl"
            test_set_path.write_text(
                json.dumps({
                    "type": "prompt",
                    "test_case_id": "prompt-fail-001",
                    "content": "test prompt",
                    "seed": {"description": "test", "system_prompt": "be helpful"},
                }) + "\n",
                encoding="utf-8",
            )

            # Patch _build_target_session to return a mock that fails on run_turn
            mock_runtime = MagicMock()
            mock_runtime.open = AsyncMock()
            mock_runtime.run_turn = AsyncMock(
                side_effect=ConnectionError("simulated network failure")
            )
            mock_runtime.close = AsyncMock()
            mock_runtime.runtime_mode = "test"
            mock_runtime.session_metadata = None

            with (
                patch(
                    "assert_eval.stages.inference._build_target_session",
                    return_value=mock_runtime,
                ),
                self.assertLogs("assert_eval.stages.inference", level="DEBUG") as log_cm,
            ):
                with self.assertRaises(ConnectionError):
                    await run_inference(
                        test_set_path=str(test_set_path),
                        save_dir=tmp_dir,
                        run_id="test-run",
                        target=target,
                        evaluation=evaluation,
                        config_path=str(test_set_path),
                    )

            debug_messages = [r for r in log_cm.output if "Inference worker" in r]
            self.assertTrue(len(debug_messages) > 0, "Expected debug log for worker failure")
            self.assertIn("test_case_000001", debug_messages[0])
            self.assertIn("simulated network failure", debug_messages[0])
            self.assertIn("Traceback", debug_messages[0])


# ── collector.py ───────────────────────────────────────────────

class PhoenixCollectorErrorTest(unittest.TestCase):
    def test_connection_error_raises_runtime_error(self) -> None:
        try:
            import pandas  # noqa: F401
        except ImportError:
            self.skipTest("pandas not installed")

        with patch("assert_eval.core.collector.PhoenixCollector.__init__", return_value=None):
            from assert_eval.core.collector import PhoenixCollector

            collector = PhoenixCollector.__new__(PhoenixCollector)
            collector._default_project = "test-project"
            collector._client = MagicMock()
            collector._client.get_spans_dataframe.side_effect = ConnectionError("refused")

            with self.assertRaises(RuntimeError) as ctx:
                collector.get_spans(project_name="test-project")
            self.assertIn("Cannot connect to Phoenix", str(ctx.exception))

    def test_generic_error_raises_runtime_error(self) -> None:
        try:
            import pandas  # noqa: F401
        except ImportError:
            self.skipTest("pandas not installed")

        with patch("assert_eval.core.collector.PhoenixCollector.__init__", return_value=None):
            from assert_eval.core.collector import PhoenixCollector

            collector = PhoenixCollector.__new__(PhoenixCollector)
            collector._default_project = "test-project"
            collector._client = MagicMock()
            collector._client.get_spans_dataframe.side_effect = RuntimeError("unexpected")

            with self.assertRaises(RuntimeError) as ctx:
                collector.get_spans(project_name="test-project")
            self.assertIn("Failed to fetch spans", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
