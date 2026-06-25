# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""End-to-end precedence tests covering load_dotenv + refresh_azure_auth_mode.

These tests pin Jake's repros from the PR #237 review (Issue 1):
``model_client`` used to freeze the Azure auth mode at module import,
which fires *before* any entrypoint loads ``.env``. The result was that
``.env``-only configurations resolved to the wrong mode silently. The
fix is to make resolution lazy and have entrypoints call
``refresh_azure_auth_mode(force=True)`` right after ``load_dotenv``.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from dotenv import load_dotenv

from assert_ai.core import azure_auth


_AZURE_AUTH_ENV_VARS = (
    "ASSERT_AZURE_USE_AAD",
    "AZURE_API_KEY",
    "AZURE_API_BASE",
    "AZURE_OPENAI_API_KEY",
)


class _DotenvHarness(unittest.TestCase):
    """Common setup: isolate the env + reset the model_client auth cache."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)
        self.addCleanup(os.chdir, self._cwd)

        # Wipe Azure-related env vars so each test starts from a known
        # baseline and the dotenv-injected values are the only source.
        self._env_patcher = patch.dict(
            os.environ,
            {k: "" for k in _AZURE_AUTH_ENV_VARS},
            clear=False,
        )
        self._env_patcher.start()
        self.addCleanup(self._env_patcher.stop)
        for var in _AZURE_AUTH_ENV_VARS:
            os.environ.pop(var, None)

        # Reset both the azure_auth provider cache and the auth-mode
        # cache so the next refresh_azure_auth_mode call resolves
        # against the test's env, not whatever a prior test cached.
        azure_auth._reset_cache_for_tests()
        self._mode_before = azure_auth._AZURE_AUTH_MODE
        self._dep_missing_before = azure_auth._AZURE_OPENAI_AAD_DEP_MISSING
        azure_auth._reset_auth_mode_cache_for_tests()
        self.addCleanup(self._restore_model_client_cache)

    def _restore_model_client_cache(self) -> None:
        azure_auth._AZURE_AUTH_MODE = self._mode_before
        azure_auth._AZURE_OPENAI_AAD_DEP_MISSING = self._dep_missing_before
        azure_auth._reset_cache_for_tests()

    def _write_dotenv(self, contents: str) -> Path:
        env_path = Path(self._tmp.name) / ".env"
        env_path.write_text(contents, encoding="utf-8")
        return env_path


class DotenvPrecedenceTest(_DotenvHarness):
    """Verify that .env values drive the resolved mode after refresh."""

    def test_dotenv_aad_flag_resolves_to_aad(self) -> None:
        """Repro: a project ``.env`` with only ``ASSERT_AZURE_USE_AAD=1`` set
        (and no shell env vars) should resolve to ``aad`` once the runner
        loads dotenv and refreshes the cache.

        Before the fix this resolved to ``aad-fallback`` because the mode
        was frozen at import time, before ``load_dotenv`` ran.
        """
        env_path = self._write_dotenv("ASSERT_AZURE_USE_AAD=1\n")

        load_dotenv(env_path, override=False)
        mode = azure_auth.refresh_azure_auth_mode(force=True)

        self.assertEqual(mode, "aad")

    def test_dotenv_api_key_resolves_to_key(self) -> None:
        """Repro: a project ``.env`` with only ``AZURE_API_KEY=...`` set
        should resolve to ``key`` after refresh. Without the fix this
        also fell through to ``aad-fallback``.
        """
        env_path = self._write_dotenv("AZURE_API_KEY=sk-test-secret\n")

        load_dotenv(env_path, override=False)
        mode = azure_auth.refresh_azure_auth_mode(force=True)

        self.assertEqual(mode, "key")

    def test_shell_key_loses_to_dotenv_aad_flag(self) -> None:
        """Repro: shell ``AZURE_API_KEY`` set + ``.env`` opts into AAD.
        The documented precedence is that the explicit AAD flag wins
        over any present API key, regardless of source. (``load_dotenv``
        is called with ``override=False`` so the shell-set ``AZURE_API_KEY``
        stays, and the new ``ASSERT_AZURE_USE_AAD=1`` from ``.env`` is
        the deciding signal.)
        """
        os.environ["AZURE_API_KEY"] = "shell-set-key"
        env_path = self._write_dotenv("ASSERT_AZURE_USE_AAD=1\n")

        load_dotenv(env_path, override=False)
        mode = azure_auth.refresh_azure_auth_mode(force=True)

        self.assertEqual(mode, "aad")


if __name__ == "__main__":
    unittest.main()
