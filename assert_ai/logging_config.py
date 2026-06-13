# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Centralized logging configuration for ASSERT."""

from __future__ import annotations

import json as json_module
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from rich.logging import RichHandler


class _JsonFormatter(logging.Formatter):
    """Emit one JSON object per log record for CI pipeline consumption."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[1] is not None:
            payload["exception"] = self.formatException(record.exc_info)
        return json_module.dumps(payload, ensure_ascii=False)


def configure_logging(
    *,
    verbose: bool = False,
    quiet: bool = False,
    log_file: Path | None = None,
    json_output: bool = False,
) -> None:
    """Set up the root logger for the ASSERT process.

    Level mapping:
        default  → INFO   (shows progress messages, warnings, errors)
        --verbose → DEBUG  (adds extra detail from core modules)
        --quiet   → WARNING (only warnings and errors)

    The interactive stderr handler uses ``sys.__stderr__`` to bypass
    OTel/Phoenix wrappers that can silently drop message bodies.
    """
    if verbose:
        level = logging.DEBUG
    elif quiet:
        level = logging.WARNING
    else:
        level = logging.INFO

    root = logging.getLogger()
    root.setLevel(level)

    # Remove any existing handlers to avoid duplicate output on
    # repeated calls (e.g. in tests).
    root.handlers.clear()

    # Interactive stderr handler — write to the unwrapped original stderr
    # so OTel/Phoenix auto-instrumentation cannot swallow our output.
    if json_output:
        console_handler = logging.StreamHandler(sys.__stderr__)
        console_handler.setFormatter(_JsonFormatter())
    else:
        console_handler = RichHandler(
            console=_make_stderr_console(),
            show_time=False,
            show_path=False,
            markup=False,
            rich_tracebacks=False,
        )
    console_handler.setLevel(level)
    root.addHandler(console_handler)

    # Optional file handler for persistent logs.
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-8s %(name)s — %(message)s")
        )
        root.addHandler(file_handler)

    # Keep noisy dependency loggers suppressed regardless of verbosity.
    # Errors from these packages are caught and wrapped by ASSERT's own modules.
    # LiteLLM installs its own console handler, so disable propagation to
    # avoid each warning appearing twice (once via litellm's handler, once
    # via ASSERT's RichHandler on the root logger).
    for _name in ("LiteLLM", "LiteLLM Router", "LiteLLM Proxy", "litellm"):
        _logger = logging.getLogger(_name)
        _logger.setLevel(logging.WARNING)
        _logger.propagate = False
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    # Phoenix/Alembic emit INFO messages during import and registration
    # ("setup plugin …", "Ensuring phoenix working directory …") that
    # are internal startup details with no value to ASSERT users.
    logging.getLogger("alembic").setLevel(logging.WARNING)
    logging.getLogger("phoenix").setLevel(logging.WARNING)

    # OTel SDK warns when ASSERT attaches a span processor to an existing
    # TracerProvider ("Overriding of current TracerProvider is not allowed").
    logging.getLogger("opentelemetry").setLevel(logging.WARNING)

    # azure-identity logs each step of its credential chain at INFO and
    # azure.core dumps every HTTP request/response (including IMDS probes
    # that return 400 outside Azure VMs, which look like errors but are
    # expected fallthroughs). Quiet these by default — auth failures still
    # surface via model_client._classify_llm_error with actionable hints.
    logging.getLogger("azure.identity").setLevel(logging.WARNING)
    logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(
        logging.WARNING
    )


def _make_stderr_console():
    """Build a Rich Console on the unwrapped stderr."""
    from rich.console import Console

    return Console(stderr=True, file=sys.__stderr__)
