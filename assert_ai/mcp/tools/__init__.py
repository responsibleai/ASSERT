# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Capability-group tool registrars for the ASSERT MCP server."""

from assert_ai.mcp.tools.author import (
    AuthorServices,
    ProbeServices,
    register_author_tools,
    register_design_tools,
    register_probe_tools,
)
from assert_ai.mcp.tools.curation import (
    CurationServices,
    register_curation_tools,
)
from assert_ai.mcp.tools.inspect import InspectServices, register_inspect_tools
from assert_ai.mcp.tools.jobs import (
    JobServices,
    register_job_control_tools,
    register_job_execute_tools,
    register_job_inspect_tools,
)
from assert_ai.mcp.tools.traces import register_trace_tools

__all__ = [
    "AuthorServices",
    "CurationServices",
    "InspectServices",
    "JobServices",
    "ProbeServices",
    "register_author_tools",
    "register_curation_tools",
    "register_design_tools",
    "register_inspect_tools",
    "register_job_control_tools",
    "register_job_execute_tools",
    "register_job_inspect_tools",
    "register_probe_tools",
    "register_trace_tools",
]
