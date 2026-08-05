# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Tests for assert_ai.core.azure_auth — precedence + optional-import behavior."""

from __future__ import annotations

import sys
import unittest
from types import ModuleType
from typing import Callable
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

    # ── family-aware resolution ────────────────────────────────

    def test_azure_ai_family_reads_azure_ai_api_key_not_azure_api_key(self) -> None:
        """An AZURE_API_KEY in the env is for an Azure OpenAI resource and
        is the wrong credential for an Azure AI Foundry endpoint. The
        family-aware resolver must ignore it for the azure_ai family and
        return aad-fallback so AAD injection kicks in.
        """
        env = {"AZURE_API_KEY": "sk-azure-openai-key"}
        self.assertEqual(
            azure_auth.resolve_azure_auth_mode(env, family="azure_ai"),
            "aad-fallback",
        )

    def test_azure_ai_family_returns_key_when_azure_ai_api_key_set(self) -> None:
        env = {"AZURE_AI_API_KEY": "user-supplied-foundry-token"}
        self.assertEqual(
            azure_auth.resolve_azure_auth_mode(env, family="azure_ai"),
            "key",
        )

    def test_flag_wins_over_azure_ai_api_key(self) -> None:
        env = {
            "ASSERT_AZURE_USE_AAD": "1",
            "AZURE_AI_API_KEY": "ignored-when-flag-set",
        }
        self.assertEqual(
            azure_auth.resolve_azure_auth_mode(env, family="azure_ai"),
            "aad",
        )

    def test_default_family_is_azure(self) -> None:
        """Existing zero-arg call sites (boot log, cache refresh) keep
        seeing the azure family (AZURE_API_KEY) behaviour unchanged."""
        env = {"AZURE_API_KEY": "sk-azure-openai-key"}
        self.assertEqual(azure_auth.resolve_azure_auth_mode(env), "key")
        # And azure_ai/* is unaffected by AZURE_API_KEY.
        self.assertEqual(
            azure_auth.resolve_azure_auth_mode(env, family="azure_ai"),
            "aad-fallback",
        )


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
        captured: dict[str, object] = {"cred_kwargs": None, "scopes": []}

        class FakeCred:
            def __init__(self, **kwargs: object) -> None:
                captured["cred_kwargs"] = kwargs

        def fake_get_bearer_token_provider(cred: object, scope: str) -> object:
            captured["scopes"].append(scope)  # type: ignore[attr-defined]
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

        # Defensive: confirm the default scope is Azure OpenAI's audience.
        self.assertEqual(captured["scopes"], [azure_auth.AZURE_OPENAI_SCOPE])

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

    def test_distinct_scopes_share_one_credential(self) -> None:
        """Adding a Foundry-scope caller must not double up on DefaultAzureCredential.

        Each scope gets its own bearer-token provider (different audience),
        but the underlying credential-chain probe runs exactly once for the
        process lifetime so the Foundry route does not pay for a second
        ``az login`` / managed-identity round trip.
        """
        construct_count = {"n": 0}
        scope_calls: list[str] = []

        class FakeCred:
            def __init__(self, **kwargs: object) -> None:
                construct_count["n"] += 1

        def fake_get_bearer_token_provider(cred: object, scope: str):
            scope_calls.append(scope)
            return lambda: f"token-for-{scope}"

        fake_identity = ModuleType("azure.identity")
        fake_identity.DefaultAzureCredential = FakeCred  # type: ignore[attr-defined]
        fake_identity.get_bearer_token_provider = fake_get_bearer_token_provider  # type: ignore[attr-defined]

        with patch.dict(
            sys.modules,
            {"azure": ModuleType("azure"), "azure.identity": fake_identity},
        ):
            openai_provider = azure_auth.get_azure_token_provider(
                azure_auth.AZURE_OPENAI_SCOPE,
            )
            foundry_provider = azure_auth.get_azure_token_provider(
                azure_auth.AZURE_FOUNDRY_SCOPE,
            )
            # Repeat calls must hit the per-scope cache.
            assert openai_provider is azure_auth.get_azure_token_provider(
                azure_auth.AZURE_OPENAI_SCOPE,
            )
            assert foundry_provider is azure_auth.get_azure_token_provider(
                azure_auth.AZURE_FOUNDRY_SCOPE,
            )

        self.assertEqual(construct_count["n"], 1)
        self.assertEqual(
            sorted(scope_calls),
            sorted([azure_auth.AZURE_OPENAI_SCOPE, azure_auth.AZURE_FOUNDRY_SCOPE]),
        )
        assert openai_provider is not None
        assert foundry_provider is not None
        self.assertIsNot(openai_provider, foundry_provider)
        self.assertEqual(
            openai_provider(), f"token-for-{azure_auth.AZURE_OPENAI_SCOPE}"
        )
        self.assertEqual(
            foundry_provider(), f"token-for-{azure_auth.AZURE_FOUNDRY_SCOPE}"
        )


# ── provenance logging ────────────────────────────────────────


def _install_fake_identity(successful_cred_cls_name: str | None) -> ModuleType:
    """Build a fake azure.identity module whose credential mimics
    ChainedTokenCredential by exposing ``_successful_credential`` after
    the bearer provider is called. ``None`` simulates a future
    azure-identity that drops the attribute.
    """

    class FakeSuccessfulCred:
        pass

    # Dynamically name the inner class so type(...).__name__ matches
    # the credential we want to test the friendly-label mapping for.
    if successful_cred_cls_name:
        FakeSuccessfulCred.__name__ = successful_cred_cls_name

    class FakeChainedCred:
        def __init__(self, **kwargs: object) -> None:
            self._successful_credential: object | None = None

        def _mark_success(self) -> None:
            if successful_cred_cls_name:
                self._successful_credential = FakeSuccessfulCred()

    def fake_get_bearer_token_provider(cred: FakeChainedCred, scope: str) -> Callable[[], str]:
        def provider() -> str:
            cred._mark_success()
            return "stub-token"

        return provider

    fake_identity = ModuleType("azure.identity")
    fake_identity.DefaultAzureCredential = FakeChainedCred  # type: ignore[attr-defined]
    fake_identity.get_bearer_token_provider = fake_get_bearer_token_provider  # type: ignore[attr-defined]
    return fake_identity


class ProvenanceLoggingTest(unittest.TestCase):
    """The wrapped provider must emit exactly one INFO line naming the
    credential that won the chain — so users get auth-path provenance
    without azure.identity's full per-step chain trace.
    """

    def setUp(self) -> None:
        azure_auth._reset_cache_for_tests()

    def tearDown(self) -> None:
        azure_auth._reset_cache_for_tests()

    def _get_provider_with_fake_identity(self, cls_name: str | None) -> Callable[[], str]:
        fake_identity = _install_fake_identity(cls_name)
        with patch.dict(
            sys.modules,
            {"azure": ModuleType("azure"), "azure.identity": fake_identity},
        ):
            provider = azure_auth.get_azure_token_provider()
        assert provider is not None
        return provider

    def test_logs_friendly_label_for_known_credential(self) -> None:
        provider = self._get_provider_with_fake_identity("AzureCliCredential")
        with self.assertLogs(azure_auth.log, level="INFO") as captured:
            self.assertEqual(provider(), "stub-token")
        joined = "\n".join(captured.output)
        self.assertIn("AzureCliCredential", joined)
        self.assertIn("az login", joined)

    def test_logs_class_name_when_friendly_label_unknown(self) -> None:
        provider = self._get_provider_with_fake_identity("SomeFutureCredential")
        with self.assertLogs(azure_auth.log, level="INFO") as captured:
            provider()
        joined = "\n".join(captured.output)
        self.assertIn("SomeFutureCredential", joined)
        # Defensive fallback label so users still know it's the chain.
        self.assertIn("credential chain", joined)

    def test_logs_generic_message_when_successful_credential_attr_missing(self) -> None:
        # Simulates a future azure-identity that no longer exposes
        # ``_successful_credential``. We must not crash and must still
        # tell the user AAD succeeded.
        provider = self._get_provider_with_fake_identity(None)
        with self.assertLogs(azure_auth.log, level="INFO") as captured:
            provider()
        joined = "\n".join(captured.output)
        self.assertIn("AAD token acquired", joined)

    def test_logs_only_once_across_many_calls(self) -> None:
        provider = self._get_provider_with_fake_identity("AzureCliCredential")
        with self.assertLogs(azure_auth.log, level="INFO") as captured:
            for _ in range(5):
                provider()
        info_lines = [line for line in captured.output if line.startswith("INFO")]
        self.assertEqual(len(info_lines), 1)


if __name__ == "__main__":
    unittest.main()
