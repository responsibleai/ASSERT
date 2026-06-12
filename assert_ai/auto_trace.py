# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Lightweight OpenInference/Phoenix auto-tracing helper.

Use ``from assert_ai import auto_trace; auto_trace.enable()`` before importing
or constructing the target agent. By default, the helper installs any available
OpenTelemetry/OpenInference instrumentors but avoids importing ``phoenix.otel``
when no Phoenix/OTLP collector is configured or reachable. This keeps local
CLI/demo startup fast while still allowing ASSERT's in-process collector to
capture spans during traced eval runs.
"""

from __future__ import annotations

import logging
import os
import socket
from importlib.metadata import entry_points

log = logging.getLogger(__name__)

_enabled = False
_instrumentors_enabled = False


def _can_connect(host: str, port: int, *, timeout: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _collector_available(*, timeout: float = 0.1) -> bool:
    """Detect whether Phoenix/OTLP export is explicitly configured or local."""
    if os.environ.get("PHOENIX_DISABLE_AUTO_INSTRUMENT") == "1":
        return False

    for name in ("PHOENIX_COLLECTOR_ENDPOINT", "OTEL_EXPORTER_OTLP_ENDPOINT"):
        if os.environ.get(name):
            log.debug("%s is set; enabling Phoenix export", name)
            return True

    if os.environ.get("ASSERT_EXPORT_TRACES", "").strip() == "1":
        log.debug("ASSERT_EXPORT_TRACES=1; enabling Phoenix export")
        return True

    ports: list[int] = []
    grpc_port = os.environ.get("PHOENIX_GRPC_PORT", "")
    if grpc_port.isnumeric():
        ports.append(int(grpc_port))
    ports.extend([4317, 6006])

    seen: set[int] = set()
    for port in ports:
        if port in seen:
            continue
        seen.add(port)
        if _can_connect("localhost", port, timeout=timeout):
            return True
    return False


def _enable_entrypoint_instrumentors() -> None:
    """Install available OpenTelemetry instrumentors without importing Phoenix."""
    global _instrumentors_enabled
    if _instrumentors_enabled:
        return

    for entry_point in entry_points().select(group="opentelemetry_instrumentor"):
        if not entry_point.value.startswith("openinference.instrumentation."):
            continue
        try:
            instrumentor_cls = entry_point.load()
            instrumentor_cls().instrument()
        except Exception:
            log.debug("Failed to enable OpenInference instrumentor %s", entry_point.name, exc_info=True)
    _instrumentors_enabled = True


def enable(
    *,
    auto_instrument: bool = True,
    timeout: float = 0.1,
    export: bool | None = None,
    **register_kwargs: object,
) -> bool:
    """Enable local OpenInference tracing and optional Phoenix export.

    Returns ``True`` when either local instrumentors or Phoenix registration ran.
    Missing or misconfigured optional tracing dependencies are non-fatal so
    examples and demos can run without Phoenix installed.
    """
    global _enabled
    if _enabled:
        return True

    if os.environ.get("PHOENIX_DISABLE_AUTO_INSTRUMENT") == "1":
        log.debug("PHOENIX_DISABLE_AUTO_INSTRUMENT=1; skipping auto-tracing")
        return False

    if auto_instrument:
        _enable_entrypoint_instrumentors()

    should_export = _collector_available(timeout=timeout) if export is None else export
    if not should_export:
        log.debug("No Phoenix/OTLP collector detected; skipping Phoenix export")
        _enabled = _instrumentors_enabled
        return _enabled

    try:
        from phoenix.otel import register
    except ImportError:
        log.debug("Phoenix tracing dependencies are not installed; skipping Phoenix export")
        _enabled = _instrumentors_enabled
        return _enabled

    try:
        register(auto_instrument=auto_instrument, **register_kwargs)
    except Exception:
        log.warning("Failed to enable Phoenix auto-tracing", exc_info=True)
        _enabled = _instrumentors_enabled
        return _enabled

    _enabled = True
    return True
