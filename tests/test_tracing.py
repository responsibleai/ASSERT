# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Tests for ``assert_ai.tracing.auto_trace`` lazy Phoenix wrapper.

Validates the gate logic across all four decision branches without
actually importing ``phoenix.otel`` (since that pulls in ~5000 modules
and would dominate the test runtime on cold caches).
"""
from __future__ import annotations

import socket
import sys
from typing import Any
from unittest.mock import patch, MagicMock

import pytest

from assert_ai import tracing


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Strip all Phoenix-related env vars before each test."""
    for var in (
        "PHOENIX_DISABLE_AUTO_INSTRUMENT",
        "PHOENIX_COLLECTOR_ENDPOINT",
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "ASSERT_AUTO_TRACE_FORCE",
    ):
        monkeypatch.delenv(var, raising=False)


def _fake_phoenix_module() -> Any:
    """Build a fake ``phoenix.otel`` module exposing a ``register`` mock."""
    mod = MagicMock()
    mod.register = MagicMock()
    return mod


def _install_fake_phoenix(monkeypatch) -> MagicMock:
    """Install a fake ``phoenix.otel`` in sys.modules; return its register mock."""
    mod = _fake_phoenix_module()
    monkeypatch.setitem(sys.modules, "phoenix.otel", mod)
    return mod.register


# ---------------------------------------------------------------------------
# phoenix_collector_available() — pure gate logic
# ---------------------------------------------------------------------------


def test_gate_skips_when_explicitly_disabled(monkeypatch):
    monkeypatch.setenv("PHOENIX_DISABLE_AUTO_INSTRUMENT", "1")
    assert tracing.phoenix_collector_available() is False


def test_gate_skips_when_disable_overrides_other_signals(monkeypatch):
    # Even if a collector endpoint is set, explicit disable wins.
    monkeypatch.setenv("PHOENIX_DISABLE_AUTO_INSTRUMENT", "1")
    monkeypatch.setenv("PHOENIX_COLLECTOR_ENDPOINT", "http://example:6006")
    assert tracing.phoenix_collector_available() is False


def test_gate_fires_when_phoenix_endpoint_env_set(monkeypatch):
    monkeypatch.setenv("PHOENIX_COLLECTOR_ENDPOINT", "http://collector:6006")
    # Probe must not be called when env var explicitly opts in.
    with patch.object(socket, "create_connection") as probe:
        assert tracing.phoenix_collector_available() is True
        probe.assert_not_called()


def test_gate_fires_when_otel_endpoint_env_set(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "https://otel.example/v1/traces")
    with patch.object(socket, "create_connection") as probe:
        assert tracing.phoenix_collector_available() is True
        probe.assert_not_called()


def test_gate_fires_when_force_env_set(monkeypatch):
    monkeypatch.setenv("ASSERT_AUTO_TRACE_FORCE", "1")
    with patch.object(socket, "create_connection") as probe:
        assert tracing.phoenix_collector_available() is True
        probe.assert_not_called()


def test_gate_fires_when_localhost_probe_succeeds():
    """TCP probe succeeds -> collector is running."""
    mock_sock = MagicMock()
    mock_sock.__enter__ = MagicMock(return_value=mock_sock)
    mock_sock.__exit__ = MagicMock(return_value=False)
    with patch.object(socket, "create_connection", return_value=mock_sock) as probe:
        assert tracing.phoenix_collector_available() is True
        probe.assert_called_once_with(("localhost", 6006), timeout=0.1)


def test_gate_skips_when_localhost_probe_refuses():
    with patch.object(socket, "create_connection", side_effect=ConnectionRefusedError):
        assert tracing.phoenix_collector_available() is False


def test_gate_skips_when_localhost_probe_times_out():
    with patch.object(socket, "create_connection", side_effect=TimeoutError):
        assert tracing.phoenix_collector_available() is False


def test_gate_skips_when_localhost_probe_oserror():
    with patch.object(socket, "create_connection", side_effect=OSError("nope")):
        assert tracing.phoenix_collector_available() is False


def test_gate_custom_host_port():
    with patch.object(socket, "create_connection", return_value=MagicMock(
        __enter__=MagicMock(return_value=None), __exit__=MagicMock(return_value=False),
    )) as probe:
        assert tracing.phoenix_collector_available(host="phoenix.svc", port=4317) is True
        probe.assert_called_once_with(("phoenix.svc", 4317), timeout=0.1)


# ---------------------------------------------------------------------------
# auto_trace() — calls into phoenix.otel.register when gate fires
# ---------------------------------------------------------------------------


def test_auto_trace_skips_when_gate_false_and_does_not_import_phoenix(monkeypatch):
    monkeypatch.setenv("PHOENIX_DISABLE_AUTO_INSTRUMENT", "1")
    # Sentinel: if phoenix.otel were imported, this fake would record it.
    register = _install_fake_phoenix(monkeypatch)
    assert tracing.auto_trace() is False
    register.assert_not_called()


def test_auto_trace_fires_when_endpoint_env_set(monkeypatch):
    monkeypatch.setenv("PHOENIX_COLLECTOR_ENDPOINT", "http://collector:6006")
    register = _install_fake_phoenix(monkeypatch)
    assert tracing.auto_trace() is True
    register.assert_called_once_with(auto_instrument=True)


def test_auto_trace_forwards_kwargs(monkeypatch):
    monkeypatch.setenv("PHOENIX_COLLECTOR_ENDPOINT", "http://collector:6006")
    register = _install_fake_phoenix(monkeypatch)
    assert tracing.auto_trace(
        project_name="my-agent",
        protocol="http/protobuf",
        batch=True,
        verbose=False,
    ) is True
    register.assert_called_once_with(
        auto_instrument=True,
        project_name="my-agent",
        protocol="http/protobuf",
        batch=True,
        verbose=False,
    )


def test_auto_trace_auto_instrument_false(monkeypatch):
    monkeypatch.setenv("PHOENIX_COLLECTOR_ENDPOINT", "http://collector:6006")
    register = _install_fake_phoenix(monkeypatch)
    assert tracing.auto_trace(auto_instrument=False) is True
    register.assert_called_once_with(auto_instrument=False)


def test_auto_trace_force_bypasses_gate(monkeypatch):
    # No env vars set, probe would fail.
    register = _install_fake_phoenix(monkeypatch)
    with patch.object(socket, "create_connection", side_effect=ConnectionRefusedError):
        assert tracing.auto_trace(force=True) is True
    register.assert_called_once_with(auto_instrument=True)


def test_auto_trace_returns_false_when_phoenix_not_installed(monkeypatch):
    monkeypatch.setenv("PHOENIX_COLLECTOR_ENDPOINT", "http://collector:6006")
    # Simulate phoenix.otel ImportError by inserting a finder that rejects it.
    monkeypatch.setitem(sys.modules, "phoenix.otel", None)
    assert tracing.auto_trace() is False


def test_auto_trace_swallows_register_no_op_when_gate_false():
    """Even with no monkey-patching at all, calling auto_trace() in a clean
    environment with no collector listening should return False and not raise.
    """
    # No env vars (autouse fixture), no collector at :6006 in CI.
    # This is the most realistic 'fresh demo laptop' scenario.
    with patch.object(socket, "create_connection", side_effect=ConnectionRefusedError):
        assert tracing.auto_trace() is False
