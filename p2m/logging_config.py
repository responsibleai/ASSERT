"""Centralized logging configuration for p2m."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from rich.logging import RichHandler


def configure_logging(
    *,
    verbose: bool = False,
    quiet: bool = False,
    log_file: Path | None = None,
) -> None:
    """Set up the root logger for the p2m process.

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

    # Keep LiteLLM debug noise suppressed regardless of verbosity.
    # Errors from LiteLLM are caught and wrapped by p2m's own modules.
    # The OpenAI SDK logs every retry at INFO; suppress to avoid flooding.
    logging.getLogger("LiteLLM").setLevel(logging.WARNING)
    logging.getLogger("LiteLLM Router").setLevel(logging.WARNING)
    logging.getLogger("LiteLLM Proxy").setLevel(logging.WARNING)
    logging.getLogger("litellm").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def _make_stderr_console():
    """Build a Rich Console on the unwrapped stderr."""
    from rich.console import Console

    return Console(stderr=True, file=sys.__stderr__)
