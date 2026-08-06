# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Explicit process-environment bootstrap for command-line entry points."""

from __future__ import annotations

import sys
from pathlib import Path


def bootstrap_environment(
    *,
    env_file: Path | None = None,
    discover_from_cwd: bool = False,
) -> None:
    """Load one dotenv source, then refresh environment-sensitive caches."""
    if env_file is not None and discover_from_cwd:
        raise ValueError("env_file and discover_from_cwd are mutually exclusive")

    from dotenv import find_dotenv, load_dotenv

    dotenv_path: str | Path | None = env_file
    if discover_from_cwd:
        dotenv_path = find_dotenv(usecwd=True) or None
    if dotenv_path is not None:
        load_dotenv(dotenv_path, override=False)

    from assert_ai.core.azure_auth import refresh_azure_auth_mode

    refresh_azure_auth_mode(force=True)

    model_client = sys.modules.get("assert_ai.core.model_client")
    if model_client is not None:
        refresh = getattr(model_client, "refresh_environment_settings", None)
        if callable(refresh):
            refresh()
