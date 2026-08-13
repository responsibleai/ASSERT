# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Shared YAML emission helpers for ASSERT.

`assert-ai init` and the ACS eval-config generator both emit YAML that
lands in a customer's repository and gets read/edited by humans. PyYAML's
default emission uses double-quoted flow scalars for strings containing
newlines or non-ASCII characters, which produces unreadable rows like
``description: "line one\\nline two \\u2014 dash"``.

This module provides a single ``dump_yaml`` entry point that:

- Uses a block-scalar (``|``) representer for any string containing a
  newline, so multi-line ``description`` / ``context`` / ``rubric``
  fields render as literal blocks instead of quoted scalars.
- Sets ``allow_unicode=True`` so em-dashes and other non-ASCII text are
  emitted verbatim rather than as ``\\uXXXX`` escapes.
- Keeps ``default_flow_style=False`` and ``sort_keys=False`` so the
  emitted mapping preserves author key order.

Call ``dump_yaml(data)`` anywhere ``yaml.dump(data, default_flow_style=False,
sort_keys=False)`` was previously used to emit a config file.
"""

from __future__ import annotations

from typing import Any

import yaml


class BlockStyleDumper(yaml.SafeDumper):
    """SafeDumper that picks the literal block style for multi-line strings."""


# YAML 1.1 treats these code points as line breaks when they appear literally
# in plain or single-quoted scalars: on reload they fold to a single space
# (or terminate the scalar), so ``allow_unicode=True`` alone is not
# round-trip safe for them. Force double-quoted style so PyYAML emits the
# escape sequences (``\N``, ``\L``, ``\P``) that survive a round trip.
_LINE_BREAK_CONTROL_CHARS = ("\u0085", "\u2028", "\u2029")


def _represent_str(dumper: yaml.SafeDumper, value: str) -> yaml.Node:
    if any(ch in value for ch in _LINE_BREAK_CONTROL_CHARS):
        style: str | None = '"'
    elif "\n" in value:
        style = "|"
    else:
        style = None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


BlockStyleDumper.add_representer(str, _represent_str)


def dump_yaml(data: Any) -> str:
    """Dump ``data`` as YAML using block scalars for multi-line strings.

    Equivalent to ``yaml.dump(data, Dumper=BlockStyleDumper,
    default_flow_style=False, sort_keys=False, allow_unicode=True)``.
    """
    return yaml.dump(
        data,
        Dumper=BlockStyleDumper,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    )
