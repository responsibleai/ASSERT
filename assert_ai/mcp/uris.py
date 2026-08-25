# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Canonical path-free ASSERT MCP resource URIs."""

from __future__ import annotations

from urllib.parse import quote, urlencode


def preset_uri(kind: str, name: str) -> str:
    return f"assert://preset/{quote(kind, safe='')}/{quote(name, safe='')}"


def config_uri(config_ref: str) -> str:
    return f"assert://config/{quote(config_ref, safe='')}"


def suite_taxonomy_uri(suite_id: str) -> str:
    return f"assert://suite/{quote(suite_id, safe='')}/taxonomy"


def suite_test_case_uri(
    suite_id: str,
    test_case_id: str,
    *,
    kind: str | None = None,
    run_id: str | None = None,
) -> str:
    uri = (
        f"assert://suite/{quote(suite_id, safe='')}/test-case/"
        f"{quote(test_case_id, safe='')}"
    )
    query = {
        key: value
        for key, value in (("kind", kind), ("run_id", run_id))
        if value is not None
    }
    return f"{uri}?{urlencode(query)}" if query else uri


def run_summary_uri(suite_id: str, run_id: str) -> str:
    return (
        f"assert://run/{quote(suite_id, safe='')}/"
        f"{quote(run_id, safe='')}/summary"
    )


def run_manifest_uri(suite_id: str, run_id: str) -> str:
    return (
        f"assert://run/{quote(suite_id, safe='')}/"
        f"{quote(run_id, safe='')}/manifest"
    )


def run_config_uri(suite_id: str, run_id: str) -> str:
    return (
        f"assert://run/{quote(suite_id, safe='')}/"
        f"{quote(run_id, safe='')}/config"
    )


def run_transcript_uri(
    suite_id: str,
    run_id: str,
    test_case_id: str,
    *,
    kind: str | None = None,
) -> str:
    uri = (
        f"assert://run/{quote(suite_id, safe='')}/"
        f"{quote(run_id, safe='')}/transcript/"
        f"{quote(test_case_id, safe='')}"
    )
    return f"{uri}?{urlencode({'kind': kind})}" if kind is not None else uri
