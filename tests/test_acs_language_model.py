# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

pytest.importorskip("acs_generator")

from assert_ai.integrations.acs.language_model import AssertLanguageModel, build_language_model


def _minimal_plan() -> dict:
    return {
        "name": "t",
        "guarded_points": ["output"],
        "annotators": [],
        "annotations": [],
        "tools": [],
        "rules": [
            {
                "point": "output",
                "decision": "deny",
                "reason": "policy_violation",
                "message": "x",
                "conditions": ['input.policy_target.value == "bomb"'],
            }
        ],
        "warnings": [],
    }


def test_fake_language_model_returns_json_string() -> None:
    language_model = build_language_model("fake", responses=[_minimal_plan()])

    raw = language_model.complete("sys", "usr")

    assert isinstance(raw, str)
    assert json.loads(raw)["name"] == "t"


def test_fake_language_model_drives_generation_engine(tmp_path) -> None:
    from acs_generator import GenerationEngine

    language_model = build_language_model("fake", responses=[_minimal_plan()])
    engine = GenerationEngine(language_model)

    engine.generate(prompt="x", out_dir=tmp_path, tool_inventory={}, write=True)

    assert (tmp_path / "manifest.yaml").is_file()


def test_build_language_model_rejects_invalid_kind_and_empty_fake() -> None:
    with pytest.raises(ValueError, match="Valid kinds"):
        build_language_model("bogus")

    with pytest.raises(ValueError, match="requires at least one response"):
        build_language_model("fake")


def test_openai_compatible_constructs_without_calling_network() -> None:
    from acs_generator import OpenAICompatibleLanguageModel

    language_model = build_language_model("openai-compatible", model="gpt-4o-mini")

    assert isinstance(language_model, OpenAICompatibleLanguageModel)
    assert language_model.model == "gpt-4o-mini"


def test_assert_language_model_constructs() -> None:
    language_model = AssertLanguageModel("azure/gpt-5.4")

    assert language_model.model == "azure/gpt-5.4"
    assert language_model.temperature == 0.0
    assert language_model.response_format_json is True


def test_assert_language_model_complete_retries_without_response_format(monkeypatch) -> None:
    import litellm

    calls: list[dict] = []

    def fake_completion(**kwargs):
        calls.append(kwargs)
        if "response_format" in kwargs:
            raise RuntimeError("response_format json_object is not supported")
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content='{"ok": true}'),
                )
            ]
        )

    monkeypatch.setattr(litellm, "completion", fake_completion)
    language_model = AssertLanguageModel("azure/gpt-5.4")

    assert language_model.complete("sys", "usr") == '{"ok": true}'
    assert len(calls) == 2
    assert calls[0]["response_format"] == {"type": "json_object"}
    assert "response_format" not in calls[1]


# ── Azure AD token provider injection (PR #237 follow-up) ──────────────
#
# The ACS LiteLLM call site is the third place in the codebase that
# hands a payload to ``litellm.completion``. The other two
# (``model_client._build_chat_payload`` and ``init._llm.chat_completion``)
# already route ``azure/*`` payloads through
# ``_maybe_inject_azure_aad_token`` so ``ASSERT_AZURE_USE_AAD=1`` works.
# These tests pin that ACS now does the same — without them the path
# silently bypassed AAD and fell back to whatever key/cred LiteLLM
# scraped from the environment.


def _ok_response() -> SimpleNamespace:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content='{"ok": true}'),
            )
        ]
    )


def _patch_aad(monkeypatch, *, mode: str, provider) -> None:
    """Force the resolved auth mode and token provider for one test."""
    from assert_ai.core import azure_auth

    monkeypatch.setattr(azure_auth, "_AZURE_AUTH_MODE", mode)
    monkeypatch.setattr(azure_auth, "get_azure_token_provider", lambda: provider)


def test_assert_language_model_injects_aad_provider_for_azure_in_aad_mode(monkeypatch) -> None:
    """``azure/*`` + ``aad`` mode → ``azure_ad_token_provider`` reaches LiteLLM."""
    import litellm

    sentinel_provider = lambda: "fake-bearer-token"
    captured: dict = {}

    def fake_completion(**kwargs):
        captured.update(kwargs)
        return _ok_response()

    monkeypatch.setattr(litellm, "completion", fake_completion)
    _patch_aad(monkeypatch, mode="aad", provider=sentinel_provider)

    AssertLanguageModel("azure/gpt-5.4").complete("sys", "usr")

    assert captured.get("azure_ad_token_provider") is sentinel_provider


def test_assert_language_model_does_not_inject_for_non_azure_models(monkeypatch) -> None:
    """Non-``azure/*`` models must not receive the token provider."""
    import litellm

    captured: dict = {}

    def fake_completion(**kwargs):
        captured.update(kwargs)
        return _ok_response()

    monkeypatch.setattr(litellm, "completion", fake_completion)
    _patch_aad(monkeypatch, mode="aad", provider=lambda: "fake-bearer-token")

    AssertLanguageModel("openai/gpt-5-mini").complete("sys", "usr")

    assert "azure_ad_token_provider" not in captured


def test_assert_language_model_does_not_inject_in_key_mode(monkeypatch) -> None:
    """``key`` mode is the legacy API-key path — no AAD injection, even for ``azure/*``."""
    import litellm

    captured: dict = {}

    def fake_completion(**kwargs):
        captured.update(kwargs)
        return _ok_response()

    monkeypatch.setattr(litellm, "completion", fake_completion)
    _patch_aad(monkeypatch, mode="key", provider=lambda: "should-not-be-used")

    AssertLanguageModel("azure/gpt-5.4").complete("sys", "usr")

    assert "azure_ad_token_provider" not in captured


def test_assert_language_model_preserves_aad_provider_on_response_format_fallback(monkeypatch) -> None:
    """The ``response_format``-less retry must still carry the injected provider.

    Older Azure deployments reject ``response_format={"type": "json_object"}``;
    the adapter strips it and retries. The retry shallow-copies the payload,
    so the injected ``azure_ad_token_provider`` callable reference must
    survive — otherwise AAD silently breaks specifically on json-mode-rejecting
    deployments.
    """
    import litellm

    sentinel_provider = lambda: "fake-bearer-token"
    calls: list[dict] = []

    def fake_completion(**kwargs):
        calls.append(kwargs)
        if "response_format" in kwargs:
            raise RuntimeError("response_format json_object is not supported")
        return _ok_response()

    monkeypatch.setattr(litellm, "completion", fake_completion)
    _patch_aad(monkeypatch, mode="aad", provider=sentinel_provider)

    AssertLanguageModel("azure/gpt-5.4").complete("sys", "usr")

    assert len(calls) == 2
    assert calls[0].get("azure_ad_token_provider") is sentinel_provider
    assert "response_format" in calls[0]
    assert calls[1].get("azure_ad_token_provider") is sentinel_provider
    assert "response_format" not in calls[1]
