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
