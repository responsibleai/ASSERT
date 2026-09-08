# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Tests for assert_ai.init._llm — AAD injection on azure/* models."""

from __future__ import annotations

import os
import sys
import types
import unittest
from typing import Any
from unittest.mock import patch

from assert_ai.core import azure_auth


def _fake_response(content: str = "hi") -> Any:
    """Build the minimal shape ``litellm.completion`` returns."""
    msg = types.SimpleNamespace(content=content)
    choice = types.SimpleNamespace(message=msg)
    return types.SimpleNamespace(choices=[choice])


class InitChatCompletionAzureAadTest(unittest.TestCase):
    """``assert-ai init`` must route azure/* through the AAD helper.

    PR #237 review (Issue 2): when ``ASSERT_AZURE_USE_AAD=1`` was set
    and ``chat_completion`` was called with an ``azure/...`` model, the
    kwargs forwarded to ``litellm.completion`` did not include
    ``azure_ad_token_provider``. The init agent therefore silently fell
    back to whatever key/cred LiteLLM scraped from the environment.
    """

    def setUp(self) -> None:
        # Force the documented ``aad`` mode and reset caches so each
        # test sees a clean resolution.
        self._env_patcher = patch.dict(
            os.environ,
            {"ASSERT_AZURE_USE_AAD": "1", "AZURE_API_KEY": ""},
            clear=False,
        )
        self._env_patcher.start()
        self.addCleanup(self._env_patcher.stop)
        os.environ.pop("AZURE_API_KEY", None)

        azure_auth._reset_cache_for_tests()
        self._mode_before = azure_auth._AZURE_AUTH_MODE
        azure_auth._AZURE_AUTH_MODE = "aad"
        self.addCleanup(self._restore)

        # Stub the token-provider lookup so we don't need azure-identity
        # installed in CI and don't have to monkey-patch ``sys.modules``
        # (which interferes with LiteLLM's pydantic-based registry).
        self._sentinel_provider: Any = lambda: "fake-bearer-token"
        self._provider_patch = patch.object(
            azure_auth,
            "get_azure_token_provider",
            return_value=self._sentinel_provider,
        )
        self._provider_patch.start()
        self.addCleanup(self._provider_patch.stop)

    def _restore(self) -> None:
        azure_auth._AZURE_AUTH_MODE = self._mode_before
        azure_auth._reset_cache_for_tests()

    def test_azure_model_gets_azure_ad_token_provider(self) -> None:
        from assert_ai.init import _llm

        captured: dict[str, Any] = {}

        def fake_completion(**kwargs: Any) -> Any:
            captured.update(kwargs)
            return _fake_response("ok")

        with patch("litellm.completion", side_effect=fake_completion):
            result = _llm.chat_completion(
                model="azure/gpt-5.4",
                messages=[{"role": "user", "content": "hi"}],
            )

        self.assertEqual(result, "ok")
        # The kwargs forwarded to litellm must include an AAD token
        # provider when the resolved mode is ``aad``. (``model_client``
        # wraps the underlying provider for provenance logging, so we
        # just assert the kwarg exists and is callable rather than
        # identity-matching the sentinel.)
        self.assertIn("azure_ad_token_provider", captured)
        self.assertTrue(callable(captured["azure_ad_token_provider"]))

    def test_non_azure_model_does_not_get_token_provider(self) -> None:
        """Sanity check: only azure/* models get AAD; openai/* untouched."""
        from assert_ai.init import _llm

        captured: dict[str, Any] = {}

        def fake_completion(**kwargs: Any) -> Any:
            captured.update(kwargs)
            return _fake_response("ok")

        with patch("litellm.completion", side_effect=fake_completion):
            _llm.chat_completion(
                model="openai/gpt-4o-mini",
                messages=[{"role": "user", "content": "hi"}],
            )

        self.assertNotIn("azure_ad_token_provider", captured)


class InitChatCompletionImportFallbackTest(unittest.TestCase):
    """The LiteLLM loader should recover from tiktoken import conflicts."""

    def test_get_litellm_module_retries_after_tiktoken_conflict(self) -> None:
        from assert_ai.core import model_client

        self.addCleanup(lambda: setattr(model_client, "_LITELLM_MODULE", None))
        model_client._LITELLM_MODULE = None
        fake_litellm = types.SimpleNamespace(suppress_debug_info=False, num_retries=0)
        calls: list[str] = []

        def fake_import(name: str, package: str | None = None) -> Any:
            calls.append(name)
            if name == "litellm" and len(calls) == 1:
                raise RuntimeError(
                    "Could not find any credentials that grant access to storage account"
                )
            if name == "litellm":
                return fake_litellm
            raise ModuleNotFoundError(name)

        with patch("assert_ai.core.model_client.importlib.import_module", side_effect=fake_import):
            result = model_client._get_litellm_module()

        self.assertIs(result, fake_litellm)
        self.assertEqual(calls, ["litellm", "litellm"])
        self.assertNotIn("litellm", sys.modules)


class InitChatCompletionAzureKeyModeTest(unittest.TestCase):
    """In ``key`` mode the helper must be a true no-op for azure/* too."""

    def setUp(self) -> None:
        self._env_patcher = patch.dict(
            os.environ,
            {"ASSERT_AZURE_USE_AAD": "", "AZURE_API_KEY": "sk-test"},
            clear=False,
        )
        self._env_patcher.start()
        self.addCleanup(self._env_patcher.stop)
        os.environ.pop("ASSERT_AZURE_USE_AAD", None)

        azure_auth._reset_cache_for_tests()
        self._mode_before = azure_auth._AZURE_AUTH_MODE
        azure_auth._AZURE_AUTH_MODE = "key"
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        azure_auth._AZURE_AUTH_MODE = self._mode_before
        azure_auth._reset_cache_for_tests()

    def test_key_mode_does_not_inject(self) -> None:
        from assert_ai.init import _llm

        captured: dict[str, Any] = {}

        def fake_completion(**kwargs: Any) -> Any:
            captured.update(kwargs)
            return _fake_response("ok")

        with patch("litellm.completion", side_effect=fake_completion):
            _llm.chat_completion(
                model="azure/gpt-5.4",
                messages=[{"role": "user", "content": "hi"}],
            )

        self.assertNotIn("azure_ad_token_provider", captured)


if __name__ == "__main__":
    unittest.main()
