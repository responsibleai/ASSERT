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

import pytest

pytest.importorskip("mcp")

from mcp.client import Client
from mcp.client._transport import TransportStreams
from mcp.client.stdio import StdioServerParameters, stdio_client

from assert_ai.mcp.models import CapabilityGroup, ServerMode
from assert_ai.mcp.server import ServerOptions, build_server
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


def test_design_group_requires_author_or_full_mode(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="require --mode author or --mode full"):
        ServerOptions.create(
            workspace_root=tmp_path,
            mode="inspect",
            enabled_groups=["design"],
        )


@pytest.mark.parametrize("mode", list(ServerMode))
def test_inspect_tools_are_registered_in_every_base_mode(
    tmp_path: Path,
    mode: ServerMode,
) -> None:
    async def run() -> set[str]:
        options = ServerOptions.create(workspace_root=tmp_path, mode=mode)
        async with Client(build_server(options), raise_exceptions=True) as client:
            tools = await client.list_tools()
            return {tool.name for tool in tools.tools}

    assert asyncio.run(run()) == EXPECTED_INSPECT_TOOLS


def test_get_server_info_protocol_round_trip(tmp_path: Path) -> None:
    async def run() -> object:
        options = ServerOptions.create(
            workspace_root=tmp_path,
            mode="full",
            enabled_groups=["analysis"],
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
        "get_preset": "25352522a3ed4ff76217c5415453c6641ad229c2dd6e4e05df4621b89ce4819c",
        "get_run": "e5216cd0085d049f8b49c54add913b6f83756c4ce59995317fe63e010ea44936",
        "get_server_info": "d51f9ff9fe235b5c53f5db71a1bba11cfb35bbc27be1f8c3552f5bee8ecc5e8d",
        "get_suite": "8f629c93e02b656052f637c3cbba9217834315a693c4f7935f6d961203b46fd0",
        "get_test_case": "11380555caaa71d5992923815a499fc08b368c02f4d4836e4761630654589148",
        "get_transcript": "aa09669e0cb99202e8dec0b858b4faa41742ecb616351c3956b0d0bd488717e8",
        "list_artifacts": "3d3bede0b7209401b15d1f39d82671092c3097a05cd901122bd46c3c42edebfc",
        "list_configs": "92f78db2533034e6bf80e1d95089460acdd40a18468d4eb06fdf055726dfef19",
        "list_failures": "d3cc3f3bcc86c110754673297d28ac1e5ccbf698668c997de0bba2d0cbd425e2",
        "list_presets": "2cb18ca86885b3dcfb230437413fef501624c0c989f26ea7f57fb56d3ff3d557",
        "list_runs": "7280687daafcd7ff5d89756c9584ca06c432a44f5a98ce8ff3ae0e4427dcf40b",
        "list_scores": "5c1951a3a3b91089b68b30e970a1b13f59bc2659a2c234db4451cbe2d5362a4d",
        "list_suites": "80f696cdb1812636e4e98089ae7a5b9fceddfc8ae994ab8e9835307c8ea12108",
        "list_test_cases": "5f46d6640668d017db8afb98d700113cfc671f93d43d57d425f82c773a6e0906",
        "read_artifact_chunk": "895f33b2e66a44cca276f94348155563fec3eab4780fe49eff101835e0c15ab7",
    }


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
