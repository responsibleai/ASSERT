# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Click commands for the optional ASSERT MCP server."""

from __future__ import annotations

import importlib
from pathlib import Path
from types import ModuleType

import click

from assert_ai.mcp.models import CapabilityGroup, ServerMode

_INSTALL_HINT = 'Install the MCP dependencies with: python -m pip install "assert-ai[mcp]"'
_EXPLICIT_GROUPS = [
    CapabilityGroup.DESIGN.value,
    CapabilityGroup.PROBE.value,
    CapabilityGroup.TRACE.value,
    CapabilityGroup.ANALYSIS.value,
    CapabilityGroup.ACS.value,
    CapabilityGroup.EXPORT.value,
]


def _load_server_module() -> ModuleType:
    """Import the MCP SDK-dependent server only when serving starts."""
    try:
        return importlib.import_module("assert_ai.mcp.server")
    except ModuleNotFoundError as exc:
        if exc.name == "mcp" or (exc.name and exc.name.startswith("mcp.")):
            raise click.ClickException(_INSTALL_HINT) from exc
        raise


@click.group(short_help="Expose ASSERT workflows through an MCP server.")
def mcp() -> None:
    """Manage the ASSERT Model Context Protocol server."""


@click.command(short_help="Serve ASSERT over MCP stdio.")
@click.option(
    "--workspace",
    type=click.Path(
        exists=True,
        file_okay=False,
        resolve_path=True,
        path_type=Path,
    ),
    default=Path("."),
    show_default=True,
    help="Workspace containing eval configs and managed artifacts.",
)
@click.option(
    "--mode",
    type=click.Choice([mode.value for mode in ServerMode], case_sensitive=False),
    default=ServerMode.INSPECT.value,
    show_default=True,
    help="Base capability set exposed by the server.",
)
@click.option(
    "--enable-group",
    "enabled_groups",
    type=click.Choice(_EXPLICIT_GROUPS, case_sensitive=False),
    multiple=True,
    help="Enable an additional capability group. Repeat as needed.",
)
def serve(workspace: Path, mode: str, enabled_groups: tuple[str, ...]) -> None:
    """Serve ASSERT over stdio; stdout is reserved for MCP protocol traffic."""
    server_module = _load_server_module()
    try:
        options = server_module.ServerOptions.create(
            workspace_root=workspace,
            mode=mode,
            enabled_groups=enabled_groups,
        )
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    server_module.run_stdio_server(options)


mcp.add_command(serve)
