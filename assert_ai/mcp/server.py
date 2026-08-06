# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""MCP v2 server factory and stdio entry point for ASSERT."""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Iterable

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from assert_ai.core.runtime_path_policy import RuntimePathPolicy
from assert_ai.core.workspace import WorkspaceService
from assert_ai.mcp.models import (
    CapabilityGroup,
    ServerInfo,
    ServerMode,
    WorkspaceInfo,
)

SERVER_NAME = "ASSERT"

_MODE_GROUPS: dict[ServerMode, tuple[CapabilityGroup, ...]] = {
    ServerMode.INSPECT: (CapabilityGroup.INSPECT,),
    ServerMode.AUTHOR: (
        CapabilityGroup.INSPECT,
        CapabilityGroup.AUTHOR,
    ),
    ServerMode.FULL: (
        CapabilityGroup.INSPECT,
        CapabilityGroup.AUTHOR,
        CapabilityGroup.DESIGN,
        CapabilityGroup.EXECUTE,
        CapabilityGroup.PROBE,
        CapabilityGroup.CURATE,
    ),
}
_GROUP_ORDER = {group: index for index, group in enumerate(CapabilityGroup)}
_AUTHOR_EXTENSION_GROUPS = {
    CapabilityGroup.DESIGN,
    CapabilityGroup.PROBE,
}


@dataclass(frozen=True)
class ServerOptions:
    """Launch-time settings fixed for the lifetime of one MCP server."""

    workspace_root: Path
    mode: ServerMode = ServerMode.INSPECT
    enabled_groups: tuple[CapabilityGroup, ...] = ()
    workspace: WorkspaceService = field(init=False, repr=False)

    def __post_init__(self) -> None:
        workspace = WorkspaceService.create(self.workspace_root)
        object.__setattr__(self, "workspace_root", workspace.root)
        object.__setattr__(self, "workspace", workspace)

    @classmethod
    def create(
        cls,
        *,
        workspace_root: str | Path,
        mode: str | ServerMode = ServerMode.INSPECT,
        enabled_groups: Iterable[str | CapabilityGroup] = (),
    ) -> "ServerOptions":
        parsed_mode = ServerMode(mode)
        parsed_groups = tuple(CapabilityGroup(group) for group in enabled_groups)
        invalid_groups = _AUTHOR_EXTENSION_GROUPS.intersection(parsed_groups)
        if parsed_mode is ServerMode.INSPECT and invalid_groups:
            names = ", ".join(sorted(group.value for group in invalid_groups))
            raise ValueError(
                f"Capability group(s) {names} require --mode author or --mode full."
            )
        return cls(
            workspace_root=Path(workspace_root),
            mode=parsed_mode,
            enabled_groups=parsed_groups,
        )

    @property
    def path_policy(self) -> RuntimePathPolicy:
        return self.workspace.path_policy

    @property
    def capability_groups(self) -> tuple[CapabilityGroup, ...]:
        groups = {*_MODE_GROUPS[self.mode], *self.enabled_groups}
        return tuple(sorted(groups, key=_GROUP_ORDER.__getitem__))


def _server_version() -> str:
    try:
        return version("assert-ai")
    except PackageNotFoundError:
        return "0.1.0"


def build_server(options: ServerOptions) -> MCPServer:
    """Build an in-process MCP server for the configured workspace."""
    server = MCPServer(
        SERVER_NAME,
        description="Local, spec-driven evaluation workflows for AI agents.",
        version=_server_version(),
    )

    @server.tool(
        title="Get ASSERT server information",
        annotations=ToolAnnotations(
            read_only_hint=True,
            open_world_hint=False,
        ),
        structured_output=True,
    )
    def get_server_info() -> ServerInfo:
        """Describe this ASSERT server's API, workspace, and capabilities."""
        return ServerInfo(
            server_version=_server_version(),
            mode=options.mode,
            enabled_capability_groups=list(options.capability_groups),
            workspace=WorkspaceInfo(
                root=options.workspace.reference(options.workspace.root),
                configs_root=options.workspace.reference(options.workspace.configs_root),
                artifacts_root=options.workspace.reference(options.workspace.artifacts_root),
                results_root=options.workspace.reference(options.workspace.results_root),
            ),
        )

    return server


def run_stdio_server(options: ServerOptions) -> None:
    """Run the configured server over stdio."""
    build_server(options).run("stdio")
