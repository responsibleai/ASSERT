# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Typed public models for the ASSERT MCP adapter."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from assert_ai.mcp import ASSERT_MCP_API_VERSION


class ServerMode(StrEnum):
    """Predefined MCP capability bundles."""

    INSPECT = "inspect"
    AUTHOR = "author"
    FULL = "full"


class CapabilityGroup(StrEnum):
    """Stable names for independently gated MCP capabilities."""

    INSPECT = "inspect"
    AUTHOR = "author"
    DESIGN = "design"
    EXECUTE = "execute"
    PROBE = "probe"
    CURATE = "curate"
    TRACE = "trace"
    ANALYSIS = "analysis"
    ACS = "acs"
    EXPORT = "export"


class WorkspaceInfo(BaseModel):
    """Workspace-relative roots managed by the MCP server."""

    model_config = ConfigDict(frozen=True)

    root: Literal["."] = "."
    configs_root: str = "evals"
    artifacts_root: str = "artifacts"
    results_root: str = "artifacts/results"


class ServerInfo(BaseModel):
    """Discovery metadata returned by ``get_server_info``."""

    model_config = ConfigDict(frozen=True)

    name: Literal["ASSERT"] = "ASSERT"
    server_version: str
    assert_mcp_api_version: Literal["1"] = ASSERT_MCP_API_VERSION
    mode: ServerMode
    enabled_capability_groups: list[CapabilityGroup]
    workspace: WorkspaceInfo = Field(default_factory=WorkspaceInfo)
    transports: list[Literal["stdio"]] = Field(default_factory=lambda: ["stdio"])
