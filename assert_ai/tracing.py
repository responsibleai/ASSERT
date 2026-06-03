# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Lazy, collector-aware wrapper around ``phoenix.otel.register``.

The "2-line" Phoenix auto-instrumentation pattern (``from phoenix.otel
import register; register(auto_instrument=True)``) has a hidden cost
on any system where ``arize-phoenix`` (the UI package) is installed
alongside ``arize-phoenix-otel``: the two share the ``phoenix.*``
namespace, so importing the OTel shim eagerly loads the UI package's
``__init__.py`` and pulls in fastapi, uvicorn, sqlalchemy, strawberry,
pandas, sklearn, ~5000 modules in total. Cold import is 30-50s. The
default exporters then spam connection errors trying to reach
``localhost:6006`` / ``localhost:4317`` when no collector is running.

This wrapper keeps the same ergonomics ("one helper call instruments
your agent") but skips the import + register entirely when there is
no collector reachable. Spans are identical when traces do flow,
because the helper forwards to ``phoenix.otel.register``.

Usage in any agent module::

    from assert_ai import auto_trace
    auto_trace()

Or with options (mirrors ``phoenix.otel.register`` signature)::

    from assert_ai import auto_trace
    auto_trace(project_name="my-agent", protocol="http/protobuf", batch=True)

Decision order for whether to import + register:

1. ``PHOENIX_DISABLE_AUTO_INSTRUMENT=1`` -> always skip.
2. ``PHOENIX_COLLECTOR_ENDPOINT`` or ``OTEL_EXPORTER_OTLP_ENDPOINT`` set
   -> always init (user explicitly asked for export).
3. ``ASSERT_AUTO_TRACE_FORCE=1`` -> always init (escape hatch for
   environments where the localhost probe is unreliable).
4. TCP-probe ``localhost:6006`` (100ms timeout) -> init iff listening.

Eval runs are unaffected: ``assert_ai/core/otel.py`` installs its own
in-process span collector and gracefully handles the case where no
``TracerProvider`` was set by ``register()``.
"""
from __future__ import annotations

import os
import socket
from typing import Any

__all__ = ["auto_trace", "phoenix_collector_available"]

_PHOENIX_DEFAULT_HOST = "localhost"
_PHOENIX_DEFAULT_PORT = 6006
_PROBE_TIMEOUT_SECONDS = 0.1


def phoenix_collector_available(
    *,
    host: str = _PHOENIX_DEFAULT_HOST,
    port: int = _PHOENIX_DEFAULT_PORT,
    timeout: float = _PROBE_TIMEOUT_SECONDS,
) -> bool:
    """Return True iff Phoenix auto-instrumentation should be enabled.

    Decision order matches the module docstring. Pure function with no
    side effects so tests can monkey-patch the socket call directly.
    """
    if os.environ.get("PHOENIX_DISABLE_AUTO_INSTRUMENT") == "1":
        return False
    if os.environ.get("PHOENIX_COLLECTOR_ENDPOINT") or os.environ.get(
        "OTEL_EXPORTER_OTLP_ENDPOINT"
    ):
        return True
    if os.environ.get("ASSERT_AUTO_TRACE_FORCE") == "1":
        return True
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def auto_trace(
    *,
    auto_instrument: bool = True,
    force: bool = False,
    **register_kwargs: Any,
) -> bool:
    """Auto-instrument the calling process via OpenInference, lazily.

    Drop-in replacement for the canonical 2-line Phoenix pattern::

        # Before:
        from phoenix.otel import register
        register(auto_instrument=True)

        # After:
        from assert_ai import auto_trace
        auto_trace()

    Skips the (expensive) ``import phoenix.otel`` entirely when no
    collector is reachable -- typical for interactive demos and CI
    smoke tests. See module docstring for the full decision order.

    Args:
        auto_instrument: forwarded to ``phoenix.otel.register``. When
            True (default), Phoenix loads every installed
            ``openinference-instrumentation-*`` package.
        force: bypass the reachability check and always attempt
            ``register()``. Useful for tests or for environments where
            the TCP probe is unreliable (e.g. behind a proxy that
            accepts but later refuses the connection).
        **register_kwargs: forwarded verbatim to
            ``phoenix.otel.register``. Common values: ``project_name``,
            ``protocol``, ``batch``, ``verbose``, ``headers``,
            ``api_key``, ``endpoint``.

    Returns:
        True if ``phoenix.otel.register`` was called successfully.
        False if the call was skipped (no collector reachable, env
        flag set, or ``arize-phoenix-otel`` not installed).
    """
    if not force and not phoenix_collector_available():
        return False
    try:
        from phoenix.otel import register
    except ImportError:
        return False
    register(auto_instrument=auto_instrument, **register_kwargs)
    return True
