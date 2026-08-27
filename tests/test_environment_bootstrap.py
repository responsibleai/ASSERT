# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import importlib
import os
import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from assert_ai.core.environment import bootstrap_environment
from assert_ai.core.model_client import refresh_environment_settings


def test_cli_discovery_loads_once_and_refreshes_auth() -> None:
    fake_model_client = SimpleNamespace(refresh_environment_settings=Mock())
    with (
        patch("dotenv.find_dotenv", return_value="C:\\workspace\\.env") as find_dotenv,
        patch("dotenv.load_dotenv") as load_dotenv,
        patch("assert_ai.core.azure_auth.refresh_azure_auth_mode") as refresh_auth,
        patch.dict(
            sys.modules,
            {"assert_ai.core.model_client": fake_model_client},
        ),
    ):
        bootstrap_environment(discover_from_cwd=True)

    find_dotenv.assert_called_once_with(usecwd=True)
    load_dotenv.assert_called_once_with("C:\\workspace\\.env", override=False)
    refresh_auth.assert_called_once_with(force=True)
    fake_model_client.refresh_environment_settings.assert_called_once_with()


def test_explicit_env_file_never_runs_discovery_with_path(tmp_path) -> None:
    env_file = tmp_path / "workspace.env"
    with (
        patch("dotenv.find_dotenv") as find_dotenv,
        patch("dotenv.load_dotenv") as load_dotenv,
        patch("assert_ai.core.azure_auth.refresh_azure_auth_mode"),
    ):
        bootstrap_environment(env_file=env_file)

    find_dotenv.assert_not_called()
    load_dotenv.assert_called_once_with(env_file, override=False)


def test_env_file_and_discovery_are_mutually_exclusive(tmp_path) -> None:
    with pytest.raises(ValueError, match="mutually exclusive"):
        bootstrap_environment(
            env_file=tmp_path / ".env",
            discover_from_cwd=True,
        )


def test_importing_runner_does_not_discover_or_load_dotenv() -> None:
    import assert_ai.runner as runner

    with (
        patch("dotenv.find_dotenv") as find_dotenv,
        patch("dotenv.load_dotenv") as load_dotenv,
    ):
        importlib.reload(runner)

    find_dotenv.assert_not_called()
    load_dotenv.assert_not_called()


def test_refreshes_model_client_settings_loaded_before_dotenv() -> None:
    with patch.dict(
        os.environ,
        {
            "AZURE_API_BASE": "https://example.openai.azure.com/openai/v1/",
            "ASSERT_PREFER_CHAT_COMPLETIONS": "",
        },
        clear=False,
    ):
        refresh_environment_settings()

        assert os.environ["AZURE_API_BASE"] == "https://example.openai.azure.com/"
