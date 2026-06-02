# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Tests for CallableSession workspace-aware module resolution.

CallableSession (and OTelTracedSession) must be able to import a target
module defined in the user's working directory or alongside the eval
config, without requiring the user to install the agent as a package.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

from assert_ai.core.model_client import Message
from assert_ai.core.session import CallableSession

_AGENT_SOURCE = """
def chat(message, history=None):
    return f"hi from {__name__}: {message}"
"""


class CallableSessionPathResolutionTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._tmp = Path(self._tmpdir.name).resolve()
        # Unique module name per test so sys.modules caching doesn't bleed
        # results between resolution paths.
        self._module_name = f"agent_under_test_{uuid.uuid4().hex[:8]}"
        (self._tmp / f"{self._module_name}.py").write_text(_AGENT_SOURCE)
        self._orig_cwd = Path.cwd()

    def tearDown(self) -> None:
        os.chdir(self._orig_cwd)
        sys.modules.pop(self._module_name, None)
        self._tmpdir.cleanup()

    async def test_resolves_from_cwd(self) -> None:
        os.chdir(self._tmp)
        session = CallableSession(callable_ref=f"{self._module_name}:chat")
        try:
            await session.open()
            result = await session.run_turn([Message(role="user", content="hello")])
        finally:
            await session.close()
        self.assertIn("hello", result.text)

    async def test_resolves_from_config_dir(self) -> None:
        # Stay in the original cwd so the cwd fallback does NOT find the
        # module; only the config-directory fallback can rescue this.
        config_path = self._tmp / "eval_config.yaml"
        session = CallableSession(
            callable_ref=f"{self._module_name}:chat",
            config_path=config_path,
        )
        try:
            await session.open()
            result = await session.run_turn([Message(role="user", content="hello")])
        finally:
            await session.close()
        self.assertIn("hello", result.text)

    async def test_missing_module_lists_search_locations(self) -> None:
        bogus_name = f"does_not_exist_{uuid.uuid4().hex[:8]}"
        config_path = self._tmp / "eval_config.yaml"
        session = CallableSession(
            callable_ref=f"{bogus_name}:chat",
            config_path=config_path,
        )
        with self.assertRaises(ValueError) as ctx:
            await session.open()
        msg = str(ctx.exception)
        self.assertIn("Searched:", msg)
        self.assertIn("Python path", msg)
        self.assertIn("Relative to config", msg)
        self.assertIn("Relative to cwd", msg)


if __name__ == "__main__":
    unittest.main()
