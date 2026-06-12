# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Tests for _ensure_hosted_trace_instrumentation in inference stage."""

import unittest
from unittest.mock import MagicMock, patch

import assert_ai
import assert_ai.stages.inference as inference_mod


class HostedTraceRegistrationTest(unittest.TestCase):
    def setUp(self) -> None:
        inference_mod._hosted_trace_registered = False

    def tearDown(self) -> None:
        inference_mod._hosted_trace_registered = False

    @patch("assert_ai.stages.inference.log")
    def test_registers_once_on_success(self, mock_log: MagicMock) -> None:
        mock_auto_trace = MagicMock()
        mock_instrumentor = MagicMock()
        mock_instrumentor_cls = MagicMock(return_value=mock_instrumentor)

        with patch.object(assert_ai, "auto_trace", mock_auto_trace, create=True), patch.dict(
            "sys.modules",
            {
                "openinference.instrumentation.litellm": MagicMock(
                    LiteLLMInstrumentor=mock_instrumentor_cls
                ),
                "openinference": MagicMock(),
                "openinference.instrumentation": MagicMock(),
            },
        ):
            inference_mod._ensure_hosted_trace_instrumentation()

        self.assertTrue(inference_mod._hosted_trace_registered)
        mock_auto_trace.enable.assert_called_once_with(auto_instrument=False, export=False)
        mock_instrumentor.instrument.assert_called_once()

        mock_auto_trace.enable.reset_mock()
        mock_instrumentor.instrument.reset_mock()
        inference_mod._ensure_hosted_trace_instrumentation()
        mock_auto_trace.enable.assert_not_called()
        mock_instrumentor.instrument.assert_not_called()

    @patch("assert_ai.stages.inference.log")
    def test_flag_stays_false_on_import_error(self, mock_log: MagicMock) -> None:
        with patch("builtins.__import__", side_effect=ImportError("missing")):
            inference_mod._ensure_hosted_trace_instrumentation()

        self.assertFalse(inference_mod._hosted_trace_registered)
        mock_log.warning.assert_called_once()

    @patch("assert_ai.stages.inference.log")
    def test_flag_stays_false_on_runtime_error(self, mock_log: MagicMock) -> None:
        mock_auto_trace = MagicMock()
        mock_auto_trace.enable.side_effect = RuntimeError("config error")

        with patch.object(assert_ai, "auto_trace", mock_auto_trace, create=True), patch.dict(
            "sys.modules",
            {
                "openinference.instrumentation.litellm": MagicMock(LiteLLMInstrumentor=MagicMock()),
                "openinference": MagicMock(),
                "openinference.instrumentation": MagicMock(),
            },
        ):
            inference_mod._ensure_hosted_trace_instrumentation()

        self.assertFalse(inference_mod._hosted_trace_registered)
        mock_log.warning.assert_called_once()
