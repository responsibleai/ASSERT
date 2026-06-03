# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Tests for assert_ai.auto_trace helper."""

from __future__ import annotations

import importlib
import sys
import unittest
from unittest.mock import MagicMock, patch


class AutoTraceTest(unittest.TestCase):
    def setUp(self) -> None:
        sys.modules.pop("assert_ai.auto_trace", None)
        self.auto_trace = importlib.import_module("assert_ai.auto_trace")
        self.auto_trace._enabled = False
        self.auto_trace._instrumentors_enabled = False

    def tearDown(self) -> None:
        self.auto_trace._enabled = False
        self.auto_trace._instrumentors_enabled = False

    def test_skips_phoenix_import_when_no_collector_is_available(self) -> None:
        mock_instrumentor = MagicMock()
        entry_point = MagicMock()
        entry_point.name = "openai"
        entry_point.value = "openinference.instrumentation.openai:OpenAIInstrumentor"
        entry_point.load.return_value = MagicMock(return_value=mock_instrumentor)
        entry_points_result = MagicMock()
        entry_points_result.select.return_value = [entry_point]

        with patch.dict("sys.modules", {"phoenix.otel": MagicMock()}, clear=False), \
             patch.dict("os.environ", {}, clear=True), \
             patch.object(self.auto_trace, "entry_points", return_value=entry_points_result), \
             patch.object(self.auto_trace.socket, "create_connection", side_effect=OSError):
            self.assertTrue(self.auto_trace.enable())

        self.assertTrue(self.auto_trace._enabled)
        mock_instrumentor.instrument.assert_called_once()
        self.assertNotIn("phoenix.otel", sys.modules)

    def test_registers_when_explicit_endpoint_is_configured(self) -> None:
        mock_register = MagicMock()
        with patch.dict("sys.modules", {"phoenix.otel": MagicMock(register=mock_register)}, clear=False), \
             patch.dict("os.environ", {"PHOENIX_COLLECTOR_ENDPOINT": "http://localhost:6006"}, clear=True), \
             patch.object(self.auto_trace, "_enable_entrypoint_instrumentors"), \
             patch.object(self.auto_trace.socket, "create_connection", side_effect=AssertionError("probe should be skipped")):
            self.assertTrue(self.auto_trace.enable())

        mock_register.assert_called_once_with(auto_instrument=True)
        self.assertTrue(self.auto_trace._enabled)

    def test_disable_env_overrides_explicit_endpoint(self) -> None:
        mock_register = MagicMock()
        with patch.dict("sys.modules", {"phoenix.otel": MagicMock(register=mock_register)}, clear=False), \
             patch.dict("os.environ", {
                 "PHOENIX_DISABLE_AUTO_INSTRUMENT": "1",
                 "PHOENIX_COLLECTOR_ENDPOINT": "http://localhost:6006",
             }, clear=True):
            self.assertFalse(self.auto_trace.enable())

        mock_register.assert_not_called()
        self.assertFalse(self.auto_trace._enabled)

    def test_probes_default_otlp_and_phoenix_ports(self) -> None:
        mock_register = MagicMock()
        calls: list[tuple[tuple[str, int], float | None]] = []

        def fake_create_connection(address: tuple[str, int], timeout: float | None = None):
            calls.append((address, timeout))
            if address == ("localhost", 4317):
                raise OSError("no grpc collector")
            connection = MagicMock()
            connection.__enter__.return_value = connection
            return connection

        with patch.dict("sys.modules", {"phoenix.otel": MagicMock(register=mock_register)}, clear=False), \
             patch.dict("os.environ", {}, clear=True), \
             patch.object(self.auto_trace, "_enable_entrypoint_instrumentors"), \
             patch.object(self.auto_trace.socket, "create_connection", side_effect=fake_create_connection):
            self.assertTrue(self.auto_trace.enable())

        self.assertEqual(calls, [(("localhost", 4317), 0.1), (("localhost", 6006), 0.1)])
        mock_register.assert_called_once_with(auto_instrument=True)


if __name__ == "__main__":
    unittest.main()
