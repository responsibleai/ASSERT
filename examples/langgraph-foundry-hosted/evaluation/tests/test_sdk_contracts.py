# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Contract tests for the example's declared Foundry/OpenAI SDK versions."""

from __future__ import annotations

import json
from importlib.metadata import version
from types import SimpleNamespace
from typing import Any

import httpx
from azure.ai.projects.models import (
    EvaluatorVersion,
    TestingCriterionAzureAIEvaluator as _TestingCriterionAzureAIEvaluator,
)
from openai import OpenAI
from openai.types.evals import CreateEvalJSONLRunDataSource
from packaging.specifiers import SpecifierSet
from packaging.version import Version

from evaluators import EvaluatorSpec, testing_criteria as _testing_criteria
from foundry import (
    DatasetRef,
    agent_run_data_source,
    dataset_run_data_source,
    scenario_run_data_source,
    trace_run_data_source,
)


def _spec() -> EvaluatorSpec:
    payload = {
        "name": "assert-pass-turn-bad-event",
        "evaluator_type": "custom",
        "categories": ["quality"],
        "display_name": "ASSERT pass: bad_event (turn)",
        "description": "Pass-oriented ASSERT dimension.",
        "supported_evaluation_levels": ["turn"],
        "metadata": {"assert_contract_sha256": "contract-hash"},
        "definition": {
            "type": "prompt",
            "prompt_text": "Return JSON.",
            "init_parameters": {
                "type": "object",
                "properties": {
                    "deployment_name": {"type": "string"},
                    "threshold": {"type": "number"},
                },
                "required": ["deployment_name", "threshold"],
            },
            "data_schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "response": {"type": "string"},
                    "tool_evidence": {},
                },
                "required": ["query", "response", "tool_evidence"],
            },
            "metrics": {
                "assert_pass": {
                    "type": "boolean",
                    "desirable_direction": "increase",
                    "threshold": 1,
                    "is_primary": True,
                }
            },
        },
    }
    return EvaluatorSpec(
        dimension_name="bad_event",
        level="turn",
        name=payload["name"],
        fingerprint="contract-hash",
        payload=payload,
    )


def _serialize_run_request(
    data_source: dict[str, Any],
    *,
    extra_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            request=request,
            json={"id": "run-1", "object": "eval.run", "status": "queued"},
        )

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = OpenAI(
        api_key="unit-test-placeholder",
        base_url="https://example.test/v1",
        http_client=http_client,
    )
    try:
        client.evals.runs.with_raw_response.create(
            eval_id="eval-1",
            name="contract-test",
            data_source=data_source,
            metadata={"route": "test"},
            extra_body=extra_body,
        )
    finally:
        client.close()
    return captured


def test_declared_sdk_versions_are_installed() -> None:
    assert Version(version("azure-ai-projects")) in SpecifierSet(">=2.6.1,<3")
    assert Version(version("openai")) in SpecifierSet(">=3,<4")


def test_official_azure_models_serialize_evaluator_contracts() -> None:
    spec = _spec()
    evaluator = EvaluatorVersion(**spec.payload)
    assert evaluator.as_dict(exclude_readonly=True) == {
        key: value for key, value in spec.payload.items() if key != "name"
    }

    criterion = _testing_criteria(
        [SimpleNamespace(name=spec.name, version="7")],
        [spec],
        model_deployment="judge",
    )[0]
    assert dict(_TestingCriterionAzureAIEvaluator(**criterion)) == criterion


def test_jsonl_payload_uses_official_openai_model_and_client_boundary() -> None:
    payload = dataset_run_data_source(
        DatasetRef(name="dataset", version="1", id="dataset-id", reused=False)
    )
    model = CreateEvalJSONLRunDataSource.model_validate(payload)
    assert model.model_dump(mode="json", exclude_none=True) == payload
    assert _serialize_run_request(payload) == {
        "name": "contract-test",
        "data_source": payload,
        "metadata": {"route": "test"},
    }


def test_azure_ai_target_completions_payload_at_client_boundary() -> None:
    dataset = DatasetRef(name="dataset", version="1", id="dataset-id", reused=False)
    payload = agent_run_data_source(
        dataset,
        agent_name="agent",
        agent_version="3",
        protocol="responses",
    )
    assert payload == {
        "type": "azure_ai_target_completions",
        "source": {"type": "file_id", "id": "dataset-id"},
        "input_messages": {
            "type": "template",
            "template": [
                {
                    "type": "message",
                    "role": "user",
                    "content": {"type": "input_text", "text": "{{item.query}}"},
                }
            ],
        },
        "target": {"type": "azure_ai_agent", "name": "agent", "version": "3"},
    }
    assert _serialize_run_request(payload)["data_source"] == payload


def test_conversation_generation_preview_payload_at_client_boundary() -> None:
    dataset = DatasetRef(name="dataset", version="1", id="dataset-id", reused=False)
    payload = scenario_run_data_source(
        dataset,
        agent_name="agent",
        agent_version=None,
        simulator_model="judge",
        max_turns=4,
    )
    assert payload == {
        "type": "azure_ai_target_completions",
        "source": {"type": "file_id", "id": "dataset-id"},
        "target": {"type": "azure_ai_agent", "name": "agent"},
        "item_generation_params": {
            "type": "conversation_gen_preview",
            "model": "judge",
            "num_conversations": 1,
            "max_turns": 4,
            "data_mapping": {
                "test_case_description": "test_case_description",
                "id": "id",
            },
        },
    }
    assert _serialize_run_request(
        payload,
        extra_body={"evaluation_level": "conversation"},
    ) == {
        "name": "contract-test",
        "data_source": payload,
        "metadata": {"route": "test"},
        "evaluation_level": "conversation",
    }


def test_turn_trace_payload_at_client_boundary() -> None:
    payload = trace_run_data_source(
        level="turn",
        trace_source={
            "type": "turn_agent_filter",
            "agent_id": "agent:2",
            "lookback_hours": 24,
            "max_traces": 50,
        },
    )
    assert payload == {
        "type": "azure_ai_traces",
        "agent_id": "agent:2",
        "lookback_hours": 24,
        "max_traces": 50,
    }
    assert _serialize_run_request(
        payload,
        extra_body={"evaluation_level": "turn"},
    )["data_source"] == payload


def test_conversation_trace_preview_payload_at_client_boundary() -> None:
    trace_source = {
        "type": "conversation_id_source",
        "conversation_ids": ["conversation-1"],
        "lookback_hours": 24,
        "end_time": "2026-09-18T12:30:00Z",
    }
    payload = trace_run_data_source(
        level="conversation",
        trace_source=trace_source,
    )
    assert payload == {
        "type": "azure_ai_trace_data_source_preview",
        "trace_source": trace_source,
    }
    assert _serialize_run_request(
        payload,
        extra_body={"evaluation_level": "conversation"},
    )["data_source"] == payload
