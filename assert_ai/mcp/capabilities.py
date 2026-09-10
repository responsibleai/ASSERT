# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Capability bundles and validation shared by MCP launch surfaces."""

from __future__ import annotations

from collections.abc import Iterable

from assert_ai.mcp.models import CapabilityGroup, ServerMode

OPTIONAL_CAPABILITY_GROUPS = (
    CapabilityGroup.DESIGN,
    CapabilityGroup.PROBE,
    CapabilityGroup.TRACE,
)

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
_SUPPORTED_GROUPS = frozenset(
    group for groups in _MODE_GROUPS.values() for group in groups
).union(OPTIONAL_CAPABILITY_GROUPS)
_AUTHOR_EXTENSION_GROUPS = {CapabilityGroup.DESIGN, CapabilityGroup.PROBE}


def resolve_capability_groups(
    mode: ServerMode,
    enabled_groups: Iterable[CapabilityGroup],
) -> tuple[CapabilityGroup, ...]:
    """Validate explicit capabilities and return their stable registration order."""
    selected = set(enabled_groups)
    unsupported = selected - _SUPPORTED_GROUPS
    if unsupported:
        names = ", ".join(sorted(group.value for group in unsupported))
        raise ValueError(f"Capability group(s) {names} are not implemented.")
    author_extensions = selected & _AUTHOR_EXTENSION_GROUPS
    if mode is ServerMode.INSPECT and author_extensions:
        names = ", ".join(sorted(group.value for group in author_extensions))
        raise ValueError(
            f"Capability group(s) {names} require --mode author or --mode full."
        )
    return tuple(sorted({*_MODE_GROUPS[mode], *selected}, key=_GROUP_ORDER.__getitem__))
