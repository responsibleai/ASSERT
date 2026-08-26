# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
from contextlib import asynccontextmanager
from copy import deepcopy
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
    "list_jobs",
    "get_job",
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
EXPECTED_TRACE_TOOLS = EXPECTED_INSPECT_TOOLS | {
    "cancel_job",
    "preflight_trace_judging",
    "retry_job",
    "start_trace_judging",
}
EXPECTED_FULL_TOOLS = EXPECTED_AUTHOR_TOOLS | {
    "design_config",
    "probe_target",
    "start_evaluation",
    "cancel_job",
    "retry_job",
    "revise_taxonomy",
    "revise_test_case",
    "bulk_revise_test_cases",
}

EXPECTED_RESOURCE_TEMPLATES = {
    "assert://preset/{kind}/{name}",
    "assert://config/{config_ref}",
    "assert://job/{job_id}/log",
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


def _seed_evaluation_workspace(root: Path) -> None:
    evals_root = root / "evals"
    evals_root.mkdir(parents=True, exist_ok=True)
    _write_jsonl(
        evals_root / "fixture.jsonl",
        [
            {
                "type": "prompt",
                "test_case_id": "case-1",
                "behavior": "local behavior",
                "seed": {"description": "hello"},
            }
        ],
    )
    (root / "agent.py").write_text(
        "def run(message, *, history=None):\n"
        "    del history\n"
        "    print('api_key=not-a-real-secret')\n"
        "    return f'local: {message}'\n",
        encoding="utf-8",
    )
    (evals_root / "job.yaml").write_text(
        json.dumps(
            {
                "suite": "mcp-job-suite",
                "pipeline": {
                    "inference": {
                        "target": {"callable": "agent:run"},
                        "test_set_path": "fixture.jsonl",
                        "concurrency": 1,
                    }
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _seed_trace_workspace(root: Path) -> None:
    evals_root = root / "evals"
    fixtures_root = root / "fixtures"
    evals_root.mkdir(parents=True, exist_ok=True)
    fixtures_root.mkdir(parents=True, exist_ok=True)
    _write_json(
        evals_root / "trace_taxonomy.json",
        {
            "behavior": {
                "name": "safe_agent",
                "definition": "The agent follows safety requirements.",
            },
            "definition_of_terms": [],
            "behavior_categories": [
                {
                    "name": "safe",
                    "definition": "The agent follows the requirement.",
                    "examples": ["The agent refuses an unsafe action."],
                    "permissible": True,
                }
            ],
        },
    )
    _write_json(
        fixtures_root / "traces.json",
        {
            "resourceSpans": [
                {
                    "scopeSpans": [
                        {
                            "spans": [
                                {
                                    "traceId": "a" * 32,
                                    "spanId": "b" * 16,
                                    "name": "agent",
                                    "startTimeUnixNano": "1",
                                    "endTimeUnixNano": "2",
                                    "attributes": [
                                        {
                                            "key": "session.id",
                                            "value": {
                                                "stringValue": "session-one"
                                            },
                                        }
                                    ],
                                }
                            ]
                        }
                    ]
                }
            ]
        },
    )
    (evals_root / "trace.yaml").write_text(
        json.dumps(
            {
                "default_model": {"name": "fixture/judge"},
                "pipeline": {
                    "judge": {
                        "model": {"name": "fixture/judge"},
                        "taxonomy_path": "trace_taxonomy.json",
                    }
                },
            },
            indent=2,
        ),
        encoding="utf-8",
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
        ("max_active_jobs", 0, "max_active_jobs must be positive"),
        ("max_queued_jobs", 0, "max_queued_jobs must be positive"),
        (
            "max_job_log_bytes",
            1024,
            "max_job_log_bytes must be between",
        ),
        (
            "max_trace_input_bytes",
            0,
            "max_trace_input_bytes must be between",
        ),
        (
            "max_trace_input_bytes",
            64 * 1024 * 1024 + 1,
            "max_trace_input_bytes must be between",
        ),
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


def test_trace_group_registers_shared_job_controls_without_evaluation_start(
    tmp_path: Path,
) -> None:
    async def run() -> dict[str, Any]:
        options = ServerOptions.create(
            workspace_root=tmp_path,
            mode="inspect",
            enabled_groups=["trace"],
        )
        async with Client(build_server(options), raise_exceptions=True) as client:
            tools = (await client.list_tools()).tools
            return {tool.name: tool for tool in tools}

    tools = asyncio.run(run())

    assert set(tools) == EXPECTED_TRACE_TOOLS
    assert "start_evaluation" not in tools
    annotations = tools["start_trace_judging"].annotations
    assert annotations is not None
    assert (
        annotations.read_only_hint,
        annotations.destructive_hint,
        annotations.idempotent_hint,
        annotations.open_world_hint,
    ) == (False, True, True, True)


def test_trace_group_does_not_control_or_launch_evaluation_jobs(
    tmp_path: Path,
) -> None:
    from assert_ai.services.job_models import NewJob
    from assert_ai.services.job_store import JobStore

    job_id = "a" * 32
    jobs_root = tmp_path / "artifacts" / "mcp" / "jobs"
    job_dir = jobs_root / job_id
    job_dir.mkdir(parents=True)
    store = JobStore(tmp_path / "artifacts" / "mcp" / "jobs.sqlite3")
    store.create_or_get(
        NewJob(
            job_id=job_id,
            idempotency_key="evaluation-request",
            request_hash="sha256:" + ("1" * 64),
            suite_id="evaluation-suite",
            run_id="evaluation-run",
            config_ref="evaluation.yaml",
            config_sha256="sha256:" + ("2" * 64),
            snapshot_path=str(job_dir / "config.yaml"),
            request_path=str(job_dir / "request.json"),
            resource_keys=("run:evaluation-suite/evaluation-run",),
        ),
        max_queued_jobs=10,
    )

    async def run() -> tuple[object, object]:
        options = ServerOptions.create(
            workspace_root=tmp_path,
            enabled_groups=["trace"],
        )
        async with Client(build_server(options), raise_exceptions=True) as client:
            await asyncio.sleep(0.1)
            detail = await client.call_tool("get_job", {"job_id": job_id})
            cancelled = await client.call_tool(
                "cancel_job",
                {"job_id": job_id},
            )
            return detail, cancelled

    detail, cancelled = asyncio.run(run())

    assert detail.structured_content["state"] == "queued"
    assert cancelled.is_error is True
    assert "CAPABILITY_DISABLED" in _error_text(cancelled)
    assert store.get(job_id).state.value == "queued"


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
    assert result.structured_content["limits"]["max_active_jobs"] == 1
    assert result.structured_content["limits"]["max_queued_jobs"] == 100
    assert result.structured_content["limits"]["max_job_log_bytes"] == 1024 * 1024
    assert (
        result.structured_content["limits"]["max_trace_input_bytes"]
        == 64 * 1024 * 1024
    )
    assert result.structured_content["limits"]["cancellation_grace_seconds"] == 10.0
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
        "get_job": "f8cde713c3889761d0898570e31a4b7256aca47b58a06d406abffd86fe513b22",
        "get_preset": "81db6723ad5065ce8a0a402d29dc2f9df7657d302e3ebe8b377f54c9d62353d0",
        "get_run": "e5216cd0085d049f8b49c54add913b6f83756c4ce59995317fe63e010ea44936",
        "get_server_info": "4e49d3bc3b8c61ef4f5884fb1b416dff5011af1dd3e278412add0db24b8ec76f",
        "get_suite": "4e4b0fba56bb596c3e1df66c0363d178623996a83617b8fe43030230c12a6316",
        "get_test_case": "11380555caaa71d5992923815a499fc08b368c02f4d4836e4761630654589148",
        "get_transcript": "aa09669e0cb99202e8dec0b858b4faa41742ecb616351c3956b0d0bd488717e8",
        "list_artifacts": "3d3bede0b7209401b15d1f39d82671092c3097a05cd901122bd46c3c42edebfc",
        "list_configs": "92f78db2533034e6bf80e1d95089460acdd40a18468d4eb06fdf055726dfef19",
        "list_failures": "d3cc3f3bcc86c110754673297d28ac1e5ccbf698668c997de0bba2d0cbd425e2",
        "list_jobs": "730f0576c25f830c71ee1a15e14c679794557ec210e68018d6038f077c8d7de6",
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
        "start_evaluation": (False, True, True, True),
        "cancel_job": (False, True, True, False),
        "retry_job": (False, True, True, True),
        "revise_taxonomy": (False, True, False, False),
        "revise_test_case": (False, True, False, False),
        "bulk_revise_test_cases": (False, True, False, False),
    }
    expected_digests = {
        "validate_config": (
            "b3068ce71b224596e9a8c25775ecaed0ab83feded27cc0fc9e26477153956203"
        ),
        "save_config": (
            "b09950417a44bf14c9bbf2702c1c00f23a18a0bfec03cab16a482733d8cf98c8"
        ),
        "preflight_evaluation": (
            "c9a686f7879c7e06a8f32c210cc02ed8e30c4fb8c6473cf77999971d114c9805"
        ),
        "design_config": (
            "1cd55a1bba06468b0aaa785cf05a445e4bf16768ff6165254ceecf0181a8392e"
        ),
        "probe_target": (
            "406d97ba84821a4e2661779dcc1211d208d2485013f8dea761c04f1fcdf59e63"
        ),
        "start_evaluation": (
            "65e49a984d49fd43a66e0c1c7f674535629e33f3246790f9f02442c3bae8716b"
        ),
        "cancel_job": (
            "568eee37c9678d0156bf27b791f769c114cdb8be03ef3a5974a6e02197568597"
        ),
        "retry_job": (
            "b9b2430a7f66535fea48a5ded5af1cab2c3baf3dd31eb60a65a535fee64e4f3e"
        ),
        "revise_taxonomy": (
            "9ef2b01aac6d7b4f31e13479c92d21cb2c6e0f8af9bb9767990c042ce4cd67bc"
        ),
        "revise_test_case": (
            "c42f702e0255fa5a4dde9288d19c8bbaada5071ada13cd2109b450bc1b463f0b"
        ),
        "bulk_revise_test_cases": (
            "bdb67f6eb579500a4026c88f3fab060e35e13559a85452901083e814f7eb5ffd"
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


def test_trace_tools_publish_stable_schemas_and_annotations(
    tmp_path: Path,
) -> None:
    async def run() -> dict[str, Any]:
        options = ServerOptions.create(
            workspace_root=tmp_path,
            enabled_groups=["trace"],
        )
        async with Client(build_server(options), raise_exceptions=True) as client:
            tools = (await client.list_tools()).tools
            return {tool.name: tool for tool in tools}

    tools = asyncio.run(run())
    expected = {
        "preflight_trace_judging": (
            (True, False, True, False),
            "828eed85b41f4cbffd1a2bfc8f7aec10ff817df77550ac2b5302db387ebf429e",
        ),
        "start_trace_judging": (
            (False, True, True, True),
            "54aeb666daf28a835d30260f2cc5334d66f7e6c5e4400cc200122b06c88eb615",
        ),
    }

    for name, (annotations, digest) in expected.items():
        tool = tools[name]
        actual = tool.annotations
        assert actual is not None
        assert (
            actual.read_only_hint,
            actual.destructive_hint,
            actual.idempotent_hint,
            actual.open_world_hint,
        ) == annotations
        assert _schema_digest(tool) == digest


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


def test_complete_versioned_curation_workflow(tmp_path: Path) -> None:
    suite_root = tmp_path / "artifacts" / "results" / "curation-suite"
    suite_root.mkdir(parents=True)
    taxonomy = {
        "behavior": {
            "name": "safe_travel",
            "definition": "Follow travel safety requirements.",
        },
        "definition_of_terms": [],
        "behavior_categories": [
            {
                "name": "safe_booking",
                "definition": "Books compliant travel.",
                "examples": ["Book a permitted flight."],
                "permissible": True,
            },
            {
                "name": "unsafe_booking",
                "definition": "Books prohibited travel.",
                "examples": ["Ignore a restriction."],
                "permissible": False,
            },
        ],
    }
    taxonomy_path = suite_root / "taxonomy.json"
    taxonomy_path.write_text(json.dumps(taxonomy), encoding="utf-8")
    (suite_root / "systematization.json").write_text(
        json.dumps(
            {
                "behavior": "safe_travel",
                "systematization": "Fixture",
                "summary_items": [],
            }
        ),
        encoding="utf-8",
    )
    test_set_path = suite_root / "test_set.jsonl"
    test_set_path.write_text(
        json.dumps(
            {
                "type": "prompt",
                "test_case_id": "test_case_000001",
                "prompt": "Book a flight.",
                "dimensions": {"behavior": "safe_booking"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (suite_root / "stratification.json").write_text(
        "{}",
        encoding="utf-8",
    )
    revised_taxonomy = deepcopy(taxonomy)
    revised_taxonomy["behavior_categories"][0]["definition"] = (
        "Books only compliant travel."
    )

    async def run() -> dict[str, Any]:
        options = ServerOptions.create(
            workspace_root=tmp_path,
            mode="full",
        )
        async with Client(build_server(options), raise_exceptions=True) as client:
            suite = await client.call_tool(
                "get_suite",
                {"suite_id": "curation-suite"},
            )
            revised = await client.call_tool(
                "revise_taxonomy",
                {
                    "suite_id": "curation-suite",
                    "taxonomy": revised_taxonomy,
                    "expected_etag": suite.structured_content[
                        "active_artifact_etags"
                    ]["taxonomy"],
                    "change_summary": "Clarify the compliant category.",
                },
            )
            first_test_set_etag = next(
                artifact["etag"]
                for artifact in revised.structured_content["artifacts"]
                if artifact["artifact_type"] == "test_set"
            )
            revised_case = await client.call_tool(
                "revise_test_case",
                {
                    "suite_id": "curation-suite",
                    "test_case_id": "test_case_000001",
                    "updates": {
                        "prompt": "Book a policy-compliant flight.",
                    },
                    "expected_etag": first_test_set_etag,
                    "change_summary": "Make the prompt explicit.",
                },
            )
            fetched = await client.call_tool(
                "get_test_case",
                {
                    "suite_id": "curation-suite",
                    "test_case_id": "test_case_000001",
                    "kind": "prompt",
                },
            )
            stale = await client.call_tool(
                "revise_test_case",
                {
                    "suite_id": "curation-suite",
                    "test_case_id": "test_case_000001",
                    "updates": {"prompt": "Stale update."},
                    "expected_etag": first_test_set_etag,
                    "change_summary": "Attempt a stale edit.",
                },
            )
            return {
                "revised": revised,
                "revised_case": revised_case,
                "fetched": fetched,
                "stale": stale,
            }

    result = asyncio.run(run())
    assert result["revised"].is_error is False
    assert [
        (item["artifact_type"], item["version"])
        for item in result["revised"].structured_content["artifacts"]
    ] == [("systematize", "v0001"), ("test_set", "v0001")]
    assert result["revised_case"].is_error is False
    assert result["revised_case"].structured_content["artifacts"][0][
        "version"
    ] == "v0002"
    assert result["fetched"].structured_content["row"]["prompt"] == (
        "Book a policy-compliant flight."
    )
    assert result["stale"].is_error is True
    assert "STALE_ETAG" in _error_text(result["stale"])


def test_complete_persisted_evaluation_workflow_through_mcp(
    tmp_path: Path,
) -> None:
    _seed_evaluation_workspace(tmp_path)

    async def run() -> dict[str, Any]:
        options = ServerOptions.create(
            workspace_root=tmp_path,
            mode="full",
            max_active_jobs=1,
            max_queued_jobs=2,
        )
        async with Client(build_server(options), raise_exceptions=True) as client:
            empty = await client.call_tool("list_jobs", {})
            started = await client.call_tool(
                "start_evaluation",
                {
                    "config_ref": "job.yaml",
                    "request_id": "mcp-integration-request",
                },
            )
            repeated = await client.call_tool(
                "start_evaluation",
                {
                    "config_ref": "job.yaml",
                    "request_id": "mcp-integration-request",
                },
            )
            conflict = await client.call_tool(
                "start_evaluation",
                {
                    "config_ref": "job.yaml",
                    "request_id": "mcp-integration-request",
                    "overrides": {"run": "different-run"},
                },
            )
            invalid_override = await client.call_tool(
                "start_evaluation",
                {
                    "config_ref": "job.yaml",
                    "request_id": "invalid-override",
                    "overrides": {"unsupported": True},
                },
            )
            job_id = started.structured_content["job"]["job_id"]
            deadline = asyncio.get_running_loop().time() + 30
            while True:
                detail = await client.call_tool(
                    "get_job",
                    {"job_id": job_id},
                )
                if detail.structured_content["state"] in {
                    "completed",
                    "failed",
                    "interrupted",
                }:
                    break
                if asyncio.get_running_loop().time() >= deadline:
                    raise AssertionError("MCP evaluation job did not finish")
                await asyncio.sleep(0.05)
            jobs = await client.call_tool(
                "list_jobs",
                {"states": ["completed"], "page_size": 1},
            )
            run = await client.call_tool(
                "get_run",
                {
                    "suite_id": detail.structured_content["suite_id"],
                    "run_id": detail.structured_content["run_id"],
                },
            )
            job_log = await client.read_resource(
                detail.structured_content["resources"]["worker_log"]
            )
            return {
                "empty": empty,
                "started": started,
                "repeated": repeated,
                "conflict": conflict,
                "invalid_override": invalid_override,
                "detail": detail,
                "jobs": jobs,
                "run": run,
                "job_log": job_log.contents[0].text,
            }

    results = asyncio.run(run())

    assert results["empty"].structured_content == {
        "items": [],
        "next_cursor": None,
    }
    started = results["started"].structured_content
    repeated = results["repeated"].structured_content
    assert started["created"] is True
    assert repeated["created"] is False
    assert repeated["job"]["job_id"] == started["job"]["job_id"]
    assert '"code":"CONFLICT"' in _error_text(results["conflict"])
    assert results["invalid_override"].is_error is True
    detail = results["detail"].structured_content
    assert detail["state"] == "completed"
    assert detail["terminal_result"]["exit_code"] == 0
    assert detail["resources"]["config"] == "assert://config/job.yaml"
    assert detail["resources"]["run_summary"].endswith("/summary")
    assert "pid" not in detail
    assert str(tmp_path) not in json.dumps(detail)
    assert results["jobs"].structured_content["items"][0]["job_id"] == (
        detail["job_id"]
    )
    assert results["run"].structured_content["state"] == "completed"
    assert "filtered tail" in results["job_log"]
    assert str(tmp_path) not in results["job_log"]
    assert "not-a-real-secret" not in results["job_log"]
    assert "[REDACTED]" in results["job_log"]


def test_complete_trace_judging_workflow_through_mcp(
    tmp_path: Path,
) -> None:
    _seed_trace_workspace(tmp_path)

    async def run() -> dict[str, Any]:
        options = ServerOptions.create(
            workspace_root=tmp_path,
            enabled_groups=["trace"],
        )
        async with Client(build_server(options), raise_exceptions=True) as client:
            preflight = await client.call_tool(
                "preflight_trace_judging",
                {
                    "config_ref": "trace.yaml",
                    "trace_ref": "fixtures/traces.json",
                    "suite_id": "trace-suite",
                    "run_id": "trace-run",
                },
            )
            started = await client.call_tool(
                "start_trace_judging",
                {
                    "config_ref": "trace.yaml",
                    "trace_ref": "fixtures/traces.json",
                    "request_id": "mcp-trace-request",
                    "suite_id": "trace-suite",
                    "run_id": "trace-run",
                },
            )
            repeated = await client.call_tool(
                "start_trace_judging",
                {
                    "config_ref": "trace.yaml",
                    "trace_ref": "fixtures/traces.json",
                    "request_id": "mcp-trace-request",
                    "suite_id": "trace-suite",
                    "run_id": "trace-run",
                },
            )
            job_id = started.structured_content["job"]["job_id"]
            deadline = asyncio.get_running_loop().time() + 30
            while True:
                detail = await client.call_tool("get_job", {"job_id": job_id})
                if detail.structured_content["state"] in {
                    "completed",
                    "failed",
                    "interrupted",
                }:
                    break
                if asyncio.get_running_loop().time() >= deadline:
                    raise AssertionError("MCP trace job did not finish")
                await asyncio.sleep(0.05)
            scores = await client.call_tool(
                "list_scores",
                {
                    "suite_id": "trace-suite",
                    "run_id": "trace-run",
                },
            )
            test_case_id = scores.structured_content["items"][0][
                "test_case_id"
            ]
            transcript = await client.call_tool(
                "get_transcript",
                {
                    "suite_id": "trace-suite",
                    "run_id": "trace-run",
                    "test_case_id": test_case_id,
                    "kind": "prompt",
                },
            )
            return {
                "preflight": preflight,
                "started": started,
                "repeated": repeated,
                "detail": detail,
                "scores": scores,
                "transcript": transcript,
            }

    results = asyncio.run(run())

    assert results["preflight"].structured_content["ready"] is True
    assert results["preflight"].structured_content["session_count"] == 1
    assert results["started"].structured_content["created"] is True
    assert results["repeated"].structured_content["created"] is False
    detail = results["detail"].structured_content
    assert detail["kind"] == "trace_judging"
    assert detail["state"] == "completed"
    assert detail["stages"]["trace_import"] == "completed"
    assert detail["stages"]["judge"] == "completed"
    score = results["scores"].structured_content["items"][0]
    assert score["judge_status"] == "scoring_skipped"
    assert score["trace_refs"] == [
        {"trace_id": "a" * 32, "span_ids": ["b" * 16]}
    ]
    transcript = results["transcript"].structured_content
    assert transcript["inference"]["trace_refs"] == score["trace_refs"]
    assert transcript["score"]["trace_refs"] == score["trace_refs"]


def test_mcp_can_cancel_a_running_evaluation(tmp_path: Path) -> None:
    _seed_evaluation_workspace(tmp_path)
    (tmp_path / "agent.py").write_text(
        "import time\n"
        "def run(message, *, history=None):\n"
        "    del history\n"
        "    time.sleep(1)\n"
        "    return message\n",
        encoding="utf-8",
    )

    async def run() -> dict[str, Any]:
        options = ServerOptions.create(
            workspace_root=tmp_path,
            mode="full",
            cancellation_grace_seconds=3,
        )
        async with Client(build_server(options), raise_exceptions=True) as client:
            started = await client.call_tool(
                "start_evaluation",
                {
                    "config_ref": "job.yaml",
                    "request_id": "cancel-through-mcp",
                },
            )
            job_id = started.structured_content["job"]["job_id"]
            deadline = asyncio.get_running_loop().time() + 15
            while True:
                detail = await client.call_tool(
                    "get_job",
                    {"job_id": job_id},
                )
                if detail.structured_content["state"] == "running":
                    break
                if asyncio.get_running_loop().time() >= deadline:
                    raise AssertionError("MCP evaluation did not start")
                await asyncio.sleep(0.05)
            cancelling = await client.call_tool(
                "cancel_job",
                {"job_id": job_id},
            )
            while True:
                detail = await client.call_tool(
                    "get_job",
                    {"job_id": job_id},
                )
                if detail.structured_content["state"] == "cancelled":
                    break
                if asyncio.get_running_loop().time() >= deadline:
                    raise AssertionError("MCP evaluation did not cancel")
                await asyncio.sleep(0.05)
            return {
                "cancelling": cancelling.structured_content,
                "terminal": detail.structured_content,
            }

    results = asyncio.run(run())

    assert results["cancelling"]["state"] == "cancelling"
    assert results["cancelling"]["cancel_requested_at"] is not None
    assert results["terminal"]["state"] == "cancelled"
    assert results["terminal"]["terminal_result"]["exit_code"] == 130
    assert results["terminal"]["stages"]["inference"] == "cancelled"


def test_mcp_retry_is_idempotent_and_records_provenance(
    tmp_path: Path,
) -> None:
    _seed_evaluation_workspace(tmp_path)
    config_path = tmp_path / "evals" / "job.yaml"
    document = json.loads(config_path.read_text(encoding="utf-8"))
    document["pipeline"]["inference"]["test_set_path"] = "missing.jsonl"
    config_path.write_text(json.dumps(document), encoding="utf-8")

    async def run() -> dict[str, Any]:
        options = ServerOptions.create(
            workspace_root=tmp_path,
            mode="full",
        )
        async with Client(build_server(options), raise_exceptions=True) as client:
            started = await client.call_tool(
                "start_evaluation",
                {
                    "config_ref": "job.yaml",
                    "request_id": "retry-original",
                },
            )
            original_id = started.structured_content["job"]["job_id"]
            deadline = asyncio.get_running_loop().time() + 20
            while True:
                original = await client.call_tool(
                    "get_job",
                    {"job_id": original_id},
                )
                if original.structured_content["state"] == "failed":
                    break
                if asyncio.get_running_loop().time() >= deadline:
                    raise AssertionError("Original MCP evaluation did not fail")
                await asyncio.sleep(0.05)
            retried = await client.call_tool(
                "retry_job",
                {
                    "job_id": original_id,
                    "request_id": "retry-attempt",
                },
            )
            repeated = await client.call_tool(
                "retry_job",
                {
                    "job_id": original_id,
                    "request_id": "retry-attempt",
                },
            )
            retry_id = retried.structured_content["job"]["job_id"]
            deadline = asyncio.get_running_loop().time() + 20
            while True:
                retry_detail = await client.call_tool(
                    "get_job",
                    {"job_id": retry_id},
                )
                if retry_detail.structured_content["state"] == "failed":
                    break
                if asyncio.get_running_loop().time() >= deadline:
                    raise AssertionError("Retried MCP evaluation did not fail")
                await asyncio.sleep(0.05)
            not_cancellable = await client.call_tool(
                "cancel_job",
                {"job_id": original_id},
            )
            return {
                "original_id": original_id,
                "retried": retried,
                "repeated": repeated,
                "not_cancellable": not_cancellable,
            }

    results = asyncio.run(run())

    retried = results["retried"].structured_content
    repeated = results["repeated"].structured_content
    assert retried["created"] is True
    assert retried["job"]["retry_of"] == results["original_id"]
    assert repeated["created"] is False
    assert repeated["job"]["job_id"] == retried["job"]["job_id"]
    assert '"code":"JOB_NOT_CANCELLABLE"' in _error_text(
        results["not_cancellable"]
    )


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
