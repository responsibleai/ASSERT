# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import asyncio
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import pytest

pytest.importorskip("mcp")

from mcp.client import Client
from mcp.client._transport import TransportStreams
from mcp.client.stdio import StdioServerParameters, stdio_client

from assert_ai.mcp.models import CapabilityGroup, ServerMode
from assert_ai.mcp.server import ServerOptions, build_server


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


def test_design_group_requires_author_or_full_mode(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="require --mode author or --mode full"):
        ServerOptions.create(
            workspace_root=tmp_path,
            mode="inspect",
            enabled_groups=["design"],
        )


def test_get_server_info_protocol_round_trip(tmp_path: Path) -> None:
    async def run() -> tuple[set[str], object]:
        options = ServerOptions.create(
            workspace_root=tmp_path,
            mode="full",
            enabled_groups=["analysis"],
        )
        async with Client(build_server(options), raise_exceptions=True) as client:
            tools = await client.list_tools()
            result = await client.call_tool("get_server_info", {})
            return {tool.name for tool in tools.tools}, result

    tool_names, result = asyncio.run(run())

    assert tool_names == {"get_server_info"}
    assert result.is_error is False
    assert result.structured_content is not None
    assert result.structured_content["assert_mcp_api_version"] == "1"
    assert result.structured_content["mode"] == "full"
    assert result.structured_content["workspace"]["root"] == "."
    assert "env_file" not in result.structured_content
    assert result.structured_content["enabled_capability_groups"] == [
        "inspect",
        "author",
        "design",
        "execute",
        "probe",
        "curate",
        "analysis",
    ]


def test_get_server_info_publishes_structured_output_schema(tmp_path: Path) -> None:
    async def run() -> object:
        options = ServerOptions.create(workspace_root=tmp_path)
        async with Client(build_server(options), raise_exceptions=True) as client:
            tools = await client.list_tools()
            return tools.tools[0]

    tool = asyncio.run(run())

    assert tool.name == "get_server_info"
    assert tool.output_schema is not None
    assert "assert_mcp_api_version" in tool.output_schema["properties"]
    assert tool.annotations is not None
    assert tool.annotations.read_only_hint is True
    assert tool.annotations.open_world_hint is False


def test_stdio_module_entry_point_keeps_protocol_wire_clean(tmp_path: Path) -> None:
    async def run() -> object:
        async with Client(_stdio_transport(tmp_path), raise_exceptions=True) as client:
            return await client.call_tool("get_server_info", {})

    result = asyncio.run(run())

    assert result.is_error is False
    assert result.structured_content is not None
    assert result.structured_content["workspace"]["root"] == "."
