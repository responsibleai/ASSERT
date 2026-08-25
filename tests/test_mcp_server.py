# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator
from unittest.mock import Mock, patch

import pytest

pytest.importorskip("mcp")

from mcp.client import Client
from mcp.client._transport import TransportStreams
from mcp.client.stdio import StdioServerParameters, stdio_client

from assert_ai.core.config_document import ConfigValidationReport
from assert_ai.mcp.models import CapabilityGroup, ServerMode
from assert_ai.mcp.server import ServerOptions, build_server
from assert_ai.services.configs import ConfigDraft
from tests.result_catalog_fixture import create_result_catalog_fixture

EXPECTED_INSPECT_TOOLS = {
    "get_server_info",
    "list_presets",
    "get_preset",
    "get_config_schema",
    "list_configs",
    "get_config",
    "list_suites",
    "get_suite",
    "list_runs",
    "get_run",
    "compare_runs",
    "list_test_cases",
    "get_test_case",
    "list_scores",
    "list_failures",
    "get_transcript",
    "list_artifacts",
    "read_artifact_chunk",
}
EXPECTED_AUTHOR_TOOLS = EXPECTED_INSPECT_TOOLS | {
    "validate_config",
    "save_config",
    "preflight_evaluation",
}
EXPECTED_FULL_TOOLS = EXPECTED_AUTHOR_TOOLS | {
    "design_config",
    "probe_target",
}

EXPECTED_RESOURCE_TEMPLATES = {
    "assert://preset/{kind}/{name}",
    "assert://config/{config_ref}",
    "assert://suite/{suite_id}/taxonomy",
    "assert://suite/{suite_id}/test-case/{test_case_id}{?kind,run_id}",
    "assert://run/{suite_id}/{run_id}/summary",
    "assert://run/{suite_id}/{run_id}/manifest",
    "assert://run/{suite_id}/{run_id}/config",
    "assert://run/{suite_id}/{run_id}/transcript/{test_case_id}{?kind}",
    "assert://artifact/{artifact_id}",
}


@asynccontextmanager
async def _stdio_transport(
    workspace: Path,
) -> AsyncIterator[TransportStreams]:
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "assert_ai.mcp",
            "--workspace",
            str(workspace),
        ],
        env={
            "PYTHONPATH": os.pathsep.join(
                filter(
                    None,
                    [
                        str(Path(__file__).resolve().parents[1]),
                        os.environ.get("PYTHONPATH"),
                    ],
                )
            )
        },
    )
    async with stdio_client(parameters) as streams:
        yield streams


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def _score(test_case_id: str, *, violation: bool) -> dict[str, Any]:
    return {
        "type": "prompt",
        "test_case_id": test_case_id,
        "behavior": "unsafe-action",
        "target": "fixture-target",
        "judge_model": "fixture-judge",
        "judge_status": "ok",
        "score_keys": ["policy_violation"],
        "not_applicable_score_keys": [],
        "verdict": {
            "dimensions": {"policy_violation": violation},
            "node_judgments": [],
        },
    }


def _seed_workspace(root: Path) -> None:
    config_path = root / "evals" / "nested" / "demo.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        "\n".join(
            [
                "behavior:",
                "  name: demo_behavior",
                "  description: Demo behavior",
                "context: 'authorization: not-a-real-secret'",
                "pipeline:",
                "  inference:",
                "    target:",
                "      model: azure/demo",
                "",
            ]
        ),
        encoding="utf-8",
    )

    suite_root = root / "artifacts" / "results" / "suite-a"
    _write_json(
        suite_root / "suite.json",
        {"created_at": "2026-08-12T00:00:00+00:00"},
    )
    _write_json(
        suite_root / "taxonomy.json",
        {
            "behavior": {
                "name": "demo_behavior",
                "description": "Demo behavior",
            },
            "behavior_categories": [
                {"name": "unsafe-action", "permissible": False},
            ],
        },
    )
    _write_jsonl(
        suite_root / "test_set.jsonl",
        [
            {
                "type": "prompt",
                "test_case_id": "p1",
                "dimensions": {
                    "behavior": "unsafe-action",
                    "region": "us",
                },
                "seed": {"prompt": "First prompt"},
            },
            {
                "type": "prompt",
                "test_case_id": "p2",
                "dimensions": {
                    "behavior": "unsafe-action",
                    "region": "eu",
                },
                "seed": {"prompt": "Second prompt"},
            },
        ],
    )

    for index, run_id in enumerate(("run-a", "run-b")):
        run_root = suite_root / run_id
        _write_json(
            run_root / "manifest.json",
            {
                "status": "completed",
                "started_at": f"2026-08-12T00:0{index}:00+00:00",
                "ended_at": f"2026-08-12T00:0{index + 1}:00+00:00",
                "stages": {
                    "inference": "completed",
                    "judge": "completed",
                },
            },
        )
        (run_root / "config.yaml").write_text(
            "pipeline: {}\napi_key: not-a-real-secret\n",
            encoding="utf-8",
        )
        _write_jsonl(
            run_root / "inference_set.jsonl",
            [
                {
                    "type": "prompt",
                    "test_case_id": "p1",
                    "target": "fixture-target",
                    "stop_reason": "completed",
                    "events": [
                        {"role": "user", "content": "First prompt"},
                        {
                            "role": "assistant",
                            "content": "First response",
                        },
                        {
                            "edit": {
                                "type": "tool_call",
                                "authorization": "not-a-real-secret",
                            }
                        },
                    ],
                },
                {
                    "type": "prompt",
                    "test_case_id": "p2",
                    "target": "fixture-target",
                    "stop_reason": "completed",
                    "events": [
                        {"role": "user", "content": "Second prompt"},
                        {
                            "role": "assistant",
                            "content": "Second response",
                        },
                    ],
                },
            ],
        )
        _write_jsonl(
            run_root / "scores.jsonl",
            [
                _score("p1", violation=index == 0),
                _score("p2", violation=False),
            ],
        )


def _schema_digest(tool: Any) -> str:
    payload = {
        "input": tool.input_schema,
        "output": tool.output_schema,
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _error_text(result: Any) -> str:
    assert result.is_error is True
    assert result.content
    return str(result.content[0].text)


def test_server_options_resolve_workspace_and_capabilities(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    options = ServerOptions.create(
        workspace_root=workspace / ".." / "workspace",
        mode="author",
        enabled_groups=["design", "trace", "design"],
    )

    assert options.workspace_root == workspace.resolve()
    assert options.mode is ServerMode.AUTHOR
    assert options.path_policy.workspace_root == workspace.resolve()
    assert options.path_policy.force_managed_outputs is True
    assert options.capability_groups == (
        CapabilityGroup.INSPECT,
        CapabilityGroup.AUTHOR,
        CapabilityGroup.DESIGN,
        CapabilityGroup.TRACE,
    )


def test_server_options_direct_constructor_preserves_workspace_root_api(
    tmp_path: Path,
) -> None:
    options = ServerOptions(workspace_root=tmp_path)

    assert options.workspace_root == tmp_path.resolve()
    assert options.workspace.root == tmp_path.resolve()
    assert options.path_policy.workspace_root == tmp_path.resolve()


def test_server_options_validate_response_limits(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must not exceed"):
        ServerOptions(
            workspace_root=tmp_path,
            max_response_bytes=4096,
            default_artifact_chunk_bytes=512,
            max_artifact_chunk_bytes=3072,
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("max_concurrency", 0, "max_concurrency must be positive"),
        (
            "max_prompt_sample_size",
            0,
            "max_prompt_sample_size must be positive",
        ),
        (
            "max_scenario_sample_size",
            0,
            "max_scenario_sample_size must be positive",
        ),
        (
            "allowed_model_patterns",
            (" ",),
            "allowed_model_patterns cannot contain empty values",
        ),
        (
            "allowed_endpoint_hosts",
            ("",),
            "allowed_endpoint_hosts cannot contain empty values",
        ),
    ],
)
def test_server_options_validate_preflight_policy(
    tmp_path: Path,
    field: str,
    value: object,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        ServerOptions(
            workspace_root=tmp_path,
            **{field: value},
        )


@pytest.mark.parametrize("group", ["design", "probe"])
def test_author_extension_groups_require_author_or_full_mode(
    tmp_path: Path,
    group: str,
) -> None:
    with pytest.raises(ValueError, match="require --mode author or --mode full"):
        ServerOptions.create(
            workspace_root=tmp_path,
            mode="inspect",
            enabled_groups=[group],
        )


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (ServerMode.INSPECT, EXPECTED_INSPECT_TOOLS),
        (ServerMode.AUTHOR, EXPECTED_AUTHOR_TOOLS),
        (ServerMode.FULL, EXPECTED_FULL_TOOLS),
    ],
)
def test_tools_are_registered_for_each_base_mode(
    tmp_path: Path,
    mode: ServerMode,
    expected: set[str],
) -> None:
    async def run() -> set[str]:
        options = ServerOptions.create(workspace_root=tmp_path, mode=mode)
        async with Client(build_server(options), raise_exceptions=True) as client:
            tools = await client.list_tools()
            return {tool.name for tool in tools.tools}

    assert asyncio.run(run()) == expected


@pytest.mark.parametrize(
    ("group", "tool"),
    [
        ("design", "design_config"),
        ("probe", "probe_target"),
    ],
)
def test_author_extension_groups_register_explicitly(
    tmp_path: Path,
    group: str,
    tool: str,
) -> None:
    async def run() -> set[str]:
        options = ServerOptions.create(
            workspace_root=tmp_path,
            mode="author",
            enabled_groups=[group],
        )
        async with Client(build_server(options), raise_exceptions=True) as client:
            tools = await client.list_tools()
            return {item.name for item in tools.tools}

    assert asyncio.run(run()) == EXPECTED_AUTHOR_TOOLS | {tool}


def test_get_server_info_protocol_round_trip(tmp_path: Path) -> None:
    async def run() -> object:
        options = ServerOptions.create(
            workspace_root=tmp_path,
            mode="full",
            enabled_groups=["analysis"],
            allowed_model_patterns=["azure/*"],
            allowed_endpoint_hosts=["api.example.test"],
        )
        async with Client(build_server(options), raise_exceptions=True) as client:
            return await client.call_tool("get_server_info", {})

    result = asyncio.run(run())

    assert result.is_error is False
    assert result.structured_content is not None
    assert result.structured_content["assert_mcp_api_version"] == "1"
    assert result.structured_content["mode"] == "full"
    assert result.structured_content["workspace"]["root"] == "."
    assert "env_file" not in result.structured_content
    assert result.structured_content["limits"]["max_page_size"] == 200
    assert result.structured_content["limits"]["max_concurrency"] == 32
    assert result.structured_content["limits"]["max_prompt_sample_size"] == 100_000
    assert result.structured_content["limits"]["max_scenario_sample_size"] == 100_000
    assert result.structured_content["limits"]["model_allowlist_enabled"] is True
    assert (
        result.structured_content["limits"]["endpoint_host_allowlist_enabled"]
        is True
    )
    assert result.structured_content["limits"]["allowed_model_patterns"] == [
        "azure/*"
    ]
    assert result.structured_content["limits"]["allowed_endpoint_hosts"] == [
        "api.example.test"
    ]
    assert result.structured_content["limits"]["target_probe_timeout_s"] == 15.0
    assert result.structured_content["target_kinds"] == [
        "callable",
        "model",
        "connector",
        "endpoint",
        "sandbox",
    ]
    assert result.structured_content["enabled_capability_groups"] == [
        "inspect",
        "author",
        "design",
        "execute",
        "probe",
        "curate",
        "analysis",
    ]


def test_all_tools_publish_stable_schemas_and_read_only_annotations(
    tmp_path: Path,
) -> None:
    async def run() -> list[Any]:
        options = ServerOptions.create(workspace_root=tmp_path)
        async with Client(build_server(options), raise_exceptions=True) as client:
            return (await client.list_tools()).tools

    tools = asyncio.run(run())

    assert {tool.name for tool in tools} == EXPECTED_INSPECT_TOOLS
    for tool in tools:
        assert tool.input_schema is not None
        assert tool.output_schema is not None
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is True
        assert tool.annotations.destructive_hint is False
        assert tool.annotations.idempotent_hint is True
        assert tool.annotations.open_world_hint is False

    digests = {tool.name: _schema_digest(tool) for tool in tools}
    assert digests == {
        # These hashes are protocol snapshots. Update them only for an
        # intentional API-v1 schema change.
        "compare_runs": "f7bfeca051f8f81bf3621936588ed906332076a3a34b550090f87c2944656ce5",
        "get_config": "bf38188871cb818e0b0cf6e28183aa728ed8d041923a158f593832d2459bd13a",
        "get_config_schema": "cca1d3a48240e20eff93a123b34d7ba92df3ed1df87f57f9eb217aa21515ec26",
        "get_preset": "81db6723ad5065ce8a0a402d29dc2f9df7657d302e3ebe8b377f54c9d62353d0",
        "get_run": "e5216cd0085d049f8b49c54add913b6f83756c4ce59995317fe63e010ea44936",
        "get_server_info": "59f160f8840051916a5e0623fe0b46ea4bb6bba5b0ecc78202325a5b1ba4bc0d",
        "get_suite": "8f629c93e02b656052f637c3cbba9217834315a693c4f7935f6d961203b46fd0",
        "get_test_case": "11380555caaa71d5992923815a499fc08b368c02f4d4836e4761630654589148",
        "get_transcript": "aa09669e0cb99202e8dec0b858b4faa41742ecb616351c3956b0d0bd488717e8",
        "list_artifacts": "3d3bede0b7209401b15d1f39d82671092c3097a05cd901122bd46c3c42edebfc",
        "list_configs": "92f78db2533034e6bf80e1d95089460acdd40a18468d4eb06fdf055726dfef19",
        "list_failures": "d3cc3f3bcc86c110754673297d28ac1e5ccbf698668c997de0bba2d0cbd425e2",
        "list_presets": "55faa31adbf7f689eb5efbf1211fa73b2474d1a0e4549ec0a836ea69919c46b1",
        "list_runs": "7280687daafcd7ff5d89756c9584ca06c432a44f5a98ce8ff3ae0e4427dcf40b",
        "list_scores": "5c1951a3a3b91089b68b30e970a1b13f59bc2659a2c234db4451cbe2d5362a4d",
        "list_suites": "80f696cdb1812636e4e98089ae7a5b9fceddfc8ae994ab8e9835307c8ea12108",
        "list_test_cases": "5f46d6640668d017db8afb98d700113cfc671f93d43d57d425f82c773a6e0906",
        "read_artifact_chunk": "895f33b2e66a44cca276f94348155563fec3eab4780fe49eff101835e0c15ab7",
    }


def test_author_tools_publish_stable_schemas_and_annotations(
    tmp_path: Path,
) -> None:
    async def run() -> dict[str, Any]:
        options = ServerOptions.create(
            workspace_root=tmp_path,
            mode="full",
        )
        async with Client(build_server(options), raise_exceptions=True) as client:
            tools = (await client.list_tools()).tools
            return {tool.name: tool for tool in tools}

    tools = asyncio.run(run())
    expected_annotations = {
        "validate_config": (True, False, True, False),
        "save_config": (False, True, False, False),
        "preflight_evaluation": (True, False, True, False),
        "design_config": (True, False, False, True),
        "probe_target": (True, False, False, True),
    }
    expected_digests = {
        "validate_config": (
            "b3068ce71b224596e9a8c25775ecaed0ab83feded27cc0fc9e26477153956203"
        ),
        "save_config": (
            "b09950417a44bf14c9bbf2702c1c00f23a18a0bfec03cab16a482733d8cf98c8"
        ),
        "preflight_evaluation": (
            "f53c4526b5df97e62f33b61a7c3a2eee375fc09eb44adabc7f5a9703d62eec64"
        ),
        "design_config": (
            "1cd55a1bba06468b0aaa785cf05a445e4bf16768ff6165254ceecf0181a8392e"
        ),
        "probe_target": (
            "406d97ba84821a4e2661779dcc1211d208d2485013f8dea761c04f1fcdf59e63"
        ),
    }

    for name, annotations in expected_annotations.items():
        tool = tools[name]
        actual = tool.annotations
        assert actual is not None
        assert (
            actual.read_only_hint,
            actual.destructive_hint,
            actual.idempotent_hint,
            actual.open_world_hint,
        ) == annotations
        assert _schema_digest(tool) == expected_digests[name]


def test_complete_author_preflight_and_probe_workflow(
    tmp_path: Path,
) -> None:
    (tmp_path / "agent.py").write_text(
        "def run(message, *, history=None):\n"
        "    return message\n",
        encoding="utf-8",
    )
    document = {
        "suite": "author-suite",
        "context": "authorization: not-a-real-secret",
        "pipeline": {
            "inference": {
                "target": {"callable": "agent:run"},
                "test_set_path": "fixtures/test_set.jsonl",
            }
        },
    }

    async def run() -> dict[str, Any]:
        options = ServerOptions.create(
            workspace_root=tmp_path,
            mode="full",
        )
        async with Client(build_server(options), raise_exceptions=True) as client:
            invalid = await client.call_tool(
                "validate_config",
                {
                    "yaml_text": (
                        "pipeline: {}\n"
                        "api_key: 'not-a-real-secret\n"
                    )
                },
            )
            valid = await client.call_tool(
                "validate_config",
                {"document": document},
            )
            saved = await client.call_tool(
                "save_config",
                {
                    "config_ref": "nested/agent.yaml",
                    "document": document,
                },
            )
            loaded = await client.call_tool(
                "get_config",
                {"config_ref": "nested/agent.yaml"},
            )
            preflight = await client.call_tool(
                "preflight_evaluation",
                {
                    "config_ref": "nested/agent.yaml",
                    "overrides": {
                        "run": "candidate-a",
                        "concurrency": 3,
                    },
                },
            )
            revised_document = {
                **document,
                "context": "Evaluate deterministic echo behavior.",
            }
            replaced = await client.call_tool(
                "save_config",
                {
                    "config_ref": "nested/agent.yaml",
                    "document": revised_document,
                    "expected_etag": loaded.structured_content["etag"],
                },
            )
            probe = await client.call_tool(
                "probe_target",
                {"config_ref": "nested/agent.yaml"},
            )
            return {
                "invalid": invalid.structured_content,
                "valid": valid.structured_content,
                "saved": saved.structured_content,
                "loaded": loaded.structured_content,
                "preflight": preflight.structured_content,
                "replaced": replaced.structured_content,
                "probe": probe.structured_content,
            }

    results = asyncio.run(run())

    assert results["invalid"]["validation"]["valid"] is False
    assert "not-a-real-secret" not in json.dumps(results["invalid"])
    assert results["valid"]["validation"]["valid"] is True
    assert results["saved"]["created"] is True
    assert results["saved"]["resource_uri"] == (
        "assert://config/nested%2Fagent.yaml"
    )
    assert results["preflight"]["ready"] is True
    assert results["preflight"]["run_id"] == "candidate-a"
    assert results["preflight"]["concurrency"] == 3
    assert results["preflight"]["target"]["kind"] == "callable"
    assert "not-a-real-secret" not in json.dumps(results["preflight"])
    assert "[REDACTED]" in json.dumps(results["preflight"])
    assert results["loaded"]["etag"] == results["saved"]["etag"]
    assert results["replaced"]["created"] is False
    assert results["replaced"]["etag"] != results["saved"]["etag"]
    assert results["probe"]["target_kind"] == "callable"
    assert results["probe"]["details"]["reference"] == "agent:run"
    assert not (tmp_path / "artifacts").exists()


def test_design_config_returns_an_unpersisted_draft(tmp_path: Path) -> None:
    draft = ConfigDraft(
        yaml=(
            "pipeline: {}\n"
            "api_key: not-a-real-secret\n"
        ),
        document={
            "pipeline": {},
            "api_key": "not-a-real-secret",
        },
        validation=ConfigValidationReport(valid=True),
    )

    async def run() -> object:
        options = ServerOptions.create(
            workspace_root=tmp_path,
            mode="author",
            enabled_groups=["design"],
        )
        async with Client(build_server(options), raise_exceptions=True) as client:
            return await client.call_tool(
                "design_config",
                {
                    "description": "Evaluate a local deterministic agent",
                    "max_turns": 3,
                },
            )

    with patch(
        "assert_ai.services.configs.ConfigService.design_config",
        new=Mock(return_value=draft),
    ) as design_config:
        result = asyncio.run(run())

    assert "not-a-real-secret" not in json.dumps(result.structured_content)
    assert "[REDACTED]" in result.structured_content["yaml"]
    assert result.structured_content["persisted"] is False
    assert result.structured_content["model_cost_incurred"] is True
    assert design_config.call_count == 1
    request = design_config.call_args.args[0]
    assert request.description == "Evaluate a local deterministic agent"
    assert request.max_turns == 3
    assert not (tmp_path / "evals").exists()


def test_design_config_enforces_operator_model_allowlist(
    tmp_path: Path,
) -> None:
    async def run() -> object:
        options = ServerOptions.create(
            workspace_root=tmp_path,
            mode="author",
            enabled_groups=["design"],
            allowed_model_patterns=["openai/*"],
        )
        async with Client(build_server(options), raise_exceptions=True) as client:
            return await client.call_tool(
                "design_config",
                {
                    "description": "Draft an evaluation",
                    "model": "azure/gpt-5.4-mini",
                },
            )

    with patch(
        "assert_ai.services.configs.ConfigService.design_config",
    ) as design_config:
        result = asyncio.run(run())

    assert '"code":"INVALID_ARGUMENT"' in _error_text(result)
    assert "not allowed by server policy" in _error_text(result)
    design_config.assert_not_called()


def test_complete_read_only_tool_workflow(tmp_path: Path) -> None:
    _seed_workspace(tmp_path)

    async def run() -> dict[str, Any]:
        async with Client(
            build_server(ServerOptions.create(workspace_root=tmp_path)),
            raise_exceptions=True,
        ) as client:
            preset_page = await client.call_tool(
                "list_presets",
                {"kind": "behavior", "page_size": 2},
            )
            preset_name = preset_page.structured_content["items"][0]["name"]
            preset = await client.call_tool(
                "get_preset",
                {"kind": "behavior", "name": preset_name},
            )
            schema = await client.call_tool("get_config_schema", {})
            configs = await client.call_tool("list_configs", {})
            config = await client.call_tool(
                "get_config",
                {"config_ref": "nested/demo.yaml"},
            )
            suites = await client.call_tool("list_suites", {"page_size": 1})
            suite = await client.call_tool(
                "get_suite",
                {"suite_id": "suite-a"},
            )
            runs = await client.call_tool(
                "list_runs",
                {"suite_id": "suite-a", "page_size": 1},
            )
            run = await client.call_tool(
                "get_run",
                {"suite_id": "suite-a", "run_id": "run-a"},
            )
            comparison = await client.call_tool(
                "compare_runs",
                {
                    "run_refs": [
                        {"suite_id": "suite-a", "run_id": "run-a"},
                        {"suite_id": "suite-a", "run_id": "run-b"},
                    ]
                },
            )
            test_cases = await client.call_tool(
                "list_test_cases",
                {
                    "suite_id": "suite-a",
                    "page_size": 1,
                    "factors": {"region": "us"},
                },
            )
            test_case = await client.call_tool(
                "get_test_case",
                {
                    "suite_id": "suite-a",
                    "test_case_id": "p1",
                    "kind": "prompt",
                },
            )
            scores = await client.call_tool(
                "list_scores",
                {
                    "suite_id": "suite-a",
                    "run_id": "run-a",
                    "dimension": "policy_violation",
                    "dimension_value": True,
                },
            )
            failures = await client.call_tool(
                "list_failures",
                {"suite_id": "suite-a", "run_id": "run-a"},
            )
            transcript = await client.call_tool(
                "get_transcript",
                {
                    "suite_id": "suite-a",
                    "run_id": "run-a",
                    "test_case_id": "p1",
                    "kind": "prompt",
                },
            )
            artifacts = await client.call_tool(
                "list_artifacts",
                {"suite_id": "suite-a", "run_id": "run-a"},
            )
            config_artifact = next(
                item
                for item in artifacts.structured_content["items"]
                if item["name"] == "config"
            )
            artifact_chunk = await client.call_tool(
                "read_artifact_chunk",
                {
                    "artifact_id": config_artifact["artifact_id"],
                    "chunk_size": 128,
                },
            )
            return {
                "preset_page": preset_page,
                "preset": preset,
                "schema": schema,
                "configs": configs,
                "config": config,
                "suites": suites,
                "suite": suite,
                "runs": runs,
                "run": run,
                "comparison": comparison,
                "test_cases": test_cases,
                "test_case": test_case,
                "scores": scores,
                "failures": failures,
                "transcript": transcript,
                "artifacts": artifacts,
                "artifact_chunk": artifact_chunk,
            }

    results = asyncio.run(run())
    assert all(not result.is_error for result in results.values())
    assert results["preset"].structured_content["document"]["kind"] == "behavior"
    assert results["schema"].structured_content["json_schema"]["$schema"].endswith(
        "2020-12/schema"
    )
    assert results["configs"].structured_content["items"][0]["config_ref"] == (
        "nested/demo.yaml"
    )
    config_payload = results["config"].structured_content
    assert "not-a-real-secret" not in json.dumps(config_payload)
    assert "[REDACTED]" in config_payload["yaml"]
    assert results["suites"].structured_content["items"][0]["suite_id"] == "suite-a"
    assert results["suite"].structured_content["run_count"] == 2
    assert results["runs"].structured_content["next_cursor"] is not None
    assert results["run"].structured_content["state"] == "completed"
    assert results["comparison"].structured_content["baseline"] == "suite-a/run-a"
    assert len(results["test_cases"].structured_content["items"]) == 1
    assert results["test_case"].structured_content["row"]["test_case_id"] == "p1"
    assert len(results["scores"].structured_content["items"]) == 1
    assert len(results["failures"].structured_content["items"]) == 1
    transcript = results["transcript"].structured_content
    assert transcript["inference"]["events"]
    assert "not-a-real-secret" not in json.dumps(transcript)
    artifacts = results["artifacts"].structured_content
    assert str(tmp_path) not in json.dumps(artifacts)
    chunk = results["artifact_chunk"].structured_content
    assert chunk["encoding"] == "utf-8"
    assert "not-a-real-secret" not in chunk["data"]
    assert "[REDACTED]" in chunk["data"]


def test_resources_are_lazy_path_free_and_readable(tmp_path: Path) -> None:
    _seed_workspace(tmp_path)

    async def run() -> tuple[set[str], set[str], dict[str, str]]:
        async with Client(
            build_server(ServerOptions.create(workspace_root=tmp_path)),
            raise_exceptions=True,
        ) as client:
            resources = await client.list_resources()
            templates = await client.list_resource_templates()
            artifacts = await client.call_tool(
                "list_artifacts",
                {"suite_id": "suite-a", "run_id": "run-a"},
            )
            config_artifact = next(
                item
                for item in artifacts.structured_content["items"]
                if item["name"] == "config"
            )
            uris = {
                "schema": "assert://schema/eval-config",
                "preset": "assert://preset/behavior/prompt_injection",
                "config": "assert://config/nested%2Fdemo.yaml",
                "taxonomy": "assert://suite/suite-a/taxonomy",
                "test_case": (
                    "assert://suite/suite-a/test-case/p1?kind=prompt"
                ),
                "summary": "assert://run/suite-a/run-a/summary",
                "manifest": "assert://run/suite-a/run-a/manifest",
                "run_config": "assert://run/suite-a/run-a/config",
                "transcript": (
                    "assert://run/suite-a/run-a/transcript/p1?kind=prompt"
                ),
                "artifact": config_artifact["resource_uri"],
            }
            contents = {}
            for name, uri in uris.items():
                result = await client.read_resource(uri)
                contents[name] = result.contents[0].text
            return (
                {str(resource.uri) for resource in resources.resources},
                {
                    template.uri_template
                    for template in templates.resource_templates
                },
                contents,
            )

    static_resources, templates, contents = asyncio.run(run())

    assert static_resources == {"assert://schema/eval-config"}
    assert templates == EXPECTED_RESOURCE_TEMPLATES
    assert "json_schema" in contents["schema"]
    assert '"kind": "behavior"' in contents["preset"]
    assert "demo_behavior" in contents["config"]
    assert "not-a-real-secret" not in json.dumps(contents)
    assert "unsafe-action" in contents["taxonomy"]
    assert '"test_case_id": "p1"' in contents["test_case"]
    assert '"run_id": "run-a"' in contents["summary"]
    assert '"status": "completed"' in contents["manifest"]
    assert "[REDACTED]" in contents["run_config"]
    assert "First response" in contents["transcript"]
    assert "[REDACTED]" in contents["artifact"]
    assert str(tmp_path) not in json.dumps(contents)


@pytest.mark.parametrize(
    ("mode", "raise_exceptions"),
    [("auto", True), ("legacy", False)],
)
def test_author_errors_are_stable_across_protocol_modes(
    tmp_path: Path,
    mode: str,
    raise_exceptions: bool,
) -> None:
    async def run() -> object:
        async with Client(
            build_server(
                ServerOptions.create(
                    workspace_root=tmp_path,
                    mode="author",
                )
            ),
            mode=mode,
            raise_exceptions=raise_exceptions,
        ) as client:
            return await client.call_tool("validate_config", {})

    text = _error_text(asyncio.run(run()))

    assert '"code":"INVALID_ARGUMENT"' in text
    assert "Provide exactly one" in text


@pytest.mark.parametrize(
    ("mode", "raise_exceptions"),
    [("auto", True), ("legacy", False)],
)
def test_service_errors_are_stable_tool_errors(
    tmp_path: Path,
    mode: str,
    raise_exceptions: bool,
) -> None:
    async def run() -> object:
        async with Client(
            build_server(ServerOptions.create(workspace_root=tmp_path)),
            mode=mode,
            raise_exceptions=raise_exceptions,
        ) as client:
            return await client.call_tool(
                "get_run",
                {"suite_id": "missing", "run_id": "missing"},
            )

    text = _error_text(asyncio.run(run()))

    assert '"code":"NOT_FOUND"' in text
    assert str(tmp_path) not in text


def test_result_cursor_reports_stale_source_through_mcp(tmp_path: Path) -> None:
    _seed_workspace(tmp_path)

    async def run() -> object:
        async with Client(
            build_server(ServerOptions.create(workspace_root=tmp_path)),
            raise_exceptions=True,
        ) as client:
            first = await client.call_tool(
                "list_test_cases",
                {"suite_id": "suite-a", "page_size": 1},
            )
            cursor = first.structured_content["next_cursor"]
            assert cursor is not None
            test_set = (
                tmp_path
                / "artifacts"
                / "results"
                / "suite-a"
                / "test_set.jsonl"
            )
            with test_set.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "type": "prompt",
                            "test_case_id": "p3",
                            "seed": {"prompt": "Third prompt"},
                        }
                    )
                    + "\n"
                )
            return await client.call_tool(
                "list_test_cases",
                {
                    "suite_id": "suite-a",
                    "cursor": cursor,
                    "page_size": 1,
                },
            )

    result = asyncio.run(run())

    assert '"code":"STALE_CURSOR"' in _error_text(result)


def test_tool_response_limit_returns_bounded_error(tmp_path: Path) -> None:
    async def run() -> object:
        options = ServerOptions(
            workspace_root=tmp_path,
            max_response_bytes=4096,
            default_artifact_chunk_bytes=512,
            max_artifact_chunk_bytes=1024,
            max_config_bytes=1024,
        )
        async with Client(build_server(options), raise_exceptions=True) as client:
            return await client.call_tool("get_config_schema", {})

    text = _error_text(asyncio.run(run()))

    assert '"code":"ARTIFACT_TOO_LARGE"' in text
    assert len(text.encode("utf-8")) < 1024


@pytest.mark.timeout(60)
def test_scale_fixture_supports_read_only_mcp_workflow(tmp_path: Path) -> None:
    fixture = create_result_catalog_fixture(
        tmp_path / "artifacts",
        suite_count=100,
        runs_per_suite=10,
        large_test_case_count=10_000,
    )

    async def run() -> tuple[object, object, object]:
        async with Client(
            build_server(ServerOptions.create(workspace_root=tmp_path)),
            raise_exceptions=True,
        ) as client:
            suites = await client.call_tool("list_suites", {"page_size": 5})
            runs = await client.call_tool(
                "list_runs",
                {"suite_id": fixture.large_suite_id, "page_size": 5},
            )
            test_case = await client.call_tool(
                "get_test_case",
                {
                    "suite_id": fixture.large_suite_id,
                    "test_case_id": fixture.last_test_case_id,
                    "kind": "prompt",
                },
            )
            return suites, runs, test_case

    suites, runs, test_case = asyncio.run(run())

    assert len(suites.structured_content["items"]) == 5
    assert suites.structured_content["next_cursor"] is not None
    assert len(runs.structured_content["items"]) == 5
    assert runs.structured_content["next_cursor"] is not None
    assert (
        test_case.structured_content["row"]["test_case_id"]
        == fixture.last_test_case_id
    )


def test_stdio_module_entry_point_keeps_protocol_wire_clean(tmp_path: Path) -> None:
    async def run() -> tuple[object, object]:
        async with Client(_stdio_transport(tmp_path), raise_exceptions=True) as client:
            info = await client.call_tool("get_server_info", {})
            presets = await client.call_tool(
                "list_presets",
                {"page_size": 1},
            )
            return info, presets

    info, presets = asyncio.run(run())

    assert info.is_error is False
    assert info.structured_content is not None
    assert info.structured_content["workspace"]["root"] == "."
    assert presets.is_error is False
    assert len(presets.structured_content["items"]) == 1
