# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Tests for assert_ai.core.azure_auth — precedence + optional-import behavior."""

from __future__ import annotations

import sys
import unittest
from types import ModuleType
from unittest.mock import patch

from assert_ai.core import azure_auth


# ── resolve_azure_auth_mode ───────────────────────────────────


class ResolveAzureAuthModeTest(unittest.TestCase):
    """Cover the documented precedence matrix exhaustively."""

    def test_flag_set_returns_aad_even_when_key_also_set(self) -> None:
        env = {"ASSERT_AZURE_USE_AAD": "1", "AZURE_API_KEY": "sk-secret"}
        self.assertEqual(azure_auth.resolve_azure_auth_mode(env), "aad")

    def test_flag_truthy_variants_return_aad(self) -> None:
        for val in ("1", "true", "TRUE", "yes", "On"):
            with self.subTest(value=val):
                env = {"ASSERT_AZURE_USE_AAD": val}
                self.assertEqual(azure_auth.resolve_azure_auth_mode(env), "aad")

    def test_key_only_returns_key(self) -> None:
        env = {"AZURE_API_KEY": "sk-secret"}
        self.assertEqual(azure_auth.resolve_azure_auth_mode(env), "key")

    def test_empty_env_returns_aad_fallback(self) -> None:
        self.assertEqual(azure_auth.resolve_azure_auth_mode({}), "aad-fallback")

    def test_blank_key_is_treated_as_unset(self) -> None:
        env = {"AZURE_API_KEY": "  "}
        self.assertEqual(azure_auth.resolve_azure_auth_mode(env), "aad-fallback")

    def test_flag_falsy_strings_do_not_force_aad(self) -> None:
        env = {"ASSERT_AZURE_USE_AAD": "0", "AZURE_API_KEY": "sk-secret"}
        self.assertEqual(azure_auth.resolve_azure_auth_mode(env), "key")

    def test_uses_os_environ_when_no_override(self) -> None:
        # Sanity check the default-argument branch.
        with patch.dict(
            "os.environ",
            {"ASSERT_AZURE_USE_AAD": "1", "AZURE_API_KEY": ""},
            clear=True,
        ):
            self.assertEqual(azure_auth.resolve_azure_auth_mode(), "aad")


# ── get_azure_token_provider ──────────────────────────────────


class GetAzureTokenProviderTest(unittest.TestCase):
    def setUp(self) -> None:
        azure_auth._reset_cache_for_tests()

    def tearDown(self) -> None:
        azure_auth._reset_cache_for_tests()

    def test_returns_none_when_azure_identity_missing(self) -> None:
        # Setting sys.modules['azure.identity'] = None forces ImportError
        # for the next `from azure.identity import ...` regardless of
        # whether the package is actually installed.
        with patch.dict(sys.modules, {"azure.identity": None}):
            provider = azure_auth.get_azure_token_provider()
        self.assertIsNone(provider)

    def test_returns_callable_when_azure_identity_available(self) -> None:
        captured: dict[str, object] = {"cred_kwargs": None, "scope": None}

        class FakeCred:
            def __init__(self, **kwargs: object) -> None:
                captured["cred_kwargs"] = kwargs

        def fake_get_bearer_token_provider(cred: object, scope: str) -> object:
            captured["scope"] = scope
            return lambda: "stub-token"

        fake_identity = ModuleType("azure.identity")
        fake_identity.DefaultAzureCredential = FakeCred  # type: ignore[attr-defined]
        fake_identity.get_bearer_token_provider = fake_get_bearer_token_provider  # type: ignore[attr-defined]

        with patch.dict(
            sys.modules,
            {"azure": ModuleType("azure"), "azure.identity": fake_identity},
        ):
            provider = azure_auth.get_azure_token_provider()

        self.assertIsNotNone(provider)
        assert provider is not None  # for type-checker
        self.assertEqual(provider(), "stub-token")

        # Defensive: non-interactive flows must be excluded so CI never hangs.
        cred_kwargs = captured["cred_kwargs"]
        assert isinstance(cred_kwargs, dict)
        self.assertTrue(cred_kwargs["exclude_interactive_browser_credential"])
        self.assertTrue(cred_kwargs["exclude_visual_studio_code_credential"])

        # Defensive: confirm the Azure OpenAI scope was requested.
        self.assertEqual(captured["scope"], azure_auth.AZURE_OPENAI_SCOPE)

    def test_provider_is_cached_across_calls(self) -> None:
        construct_count = {"n": 0}

        class FakeCred:
            def __init__(self, **kwargs: object) -> None:
                construct_count["n"] += 1

        fake_identity = ModuleType("azure.identity")
        fake_identity.DefaultAzureCredential = FakeCred  # type: ignore[attr-defined]
        fake_identity.get_bearer_token_provider = (  # type: ignore[attr-defined]
            lambda cred, scope: lambda: "t"
        )

        with patch.dict(
            sys.modules,
            {"azure": ModuleType("azure"), "azure.identity": fake_identity},
        ):
            azure_auth.get_azure_token_provider()
            azure_auth.get_azure_token_provider()
            azure_auth.get_azure_token_provider()

        self.assertEqual(construct_count["n"], 1)

    def test_missing_dep_is_also_cached(self) -> None:
        """Confirm we don't re-attempt the failed import on every call."""
        attempt_count = {"n": 0}

        original_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

        def counting_import(name: str, *args: object, **kwargs: object) -> object:
            if name == "azure.identity":
                attempt_count["n"] += 1
                raise ImportError("simulated missing dep")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=counting_import):
            azure_auth.get_azure_token_provider()
            azure_auth.get_azure_token_provider()
            azure_auth.get_azure_token_provider()

        self.assertEqual(attempt_count["n"], 1)


if __name__ == "__main__":
    unittest.main()
