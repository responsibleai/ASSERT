# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Shared result projections and YAML rendering for MCP responses."""

from __future__ import annotations

from typing import Any

import yaml

from assert_ai.mcp.uris import run_config_uri, run_manifest_uri, run_summary_uri


def dump_yaml(document: dict[str, Any]) -> str:
    text = yaml.safe_dump(
        document,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    )
    return text if text.endswith("\n") else text + "\n"


def public_suite(summary: dict[str, Any]) -> dict[str, Any]:
    """Expose curation ETags without exposing internal artifact references."""
    payload = dict(summary)
    sources = payload.get("sources")
    artifact_etags: dict[str, str] = {}
    if isinstance(sources, dict):
        for name in ("taxonomy", "test_set"):
            source = sources.get(name)
            sha256 = source.get("sha256") if isinstance(source, dict) else None
            if (
                isinstance(sha256, str)
                and len(sha256) == 64
                and all(character in "0123456789abcdef" for character in sha256)
            ):
                artifact_etags[name] = f"sha256:{sha256}"
    payload["active_artifact_etags"] = artifact_etags
    for key in (
        "artifact_versions",
        "sources",
        "run_set_identity",
        "run_catalog_identity",
    ):
        payload.pop(key, None)
    return payload


def public_run(summary: dict[str, Any]) -> dict[str, Any]:
    """Project run metadata consistently for tool and resource responses."""
    payload = dict(summary)
    for key in ("artifact_versions", "sources", "indexes"):
        payload.pop(key, None)
    return payload


def run_resources(suite_id: str, run_id: str) -> dict[str, str]:
    return {
        "summary": run_summary_uri(suite_id, run_id),
        "manifest": run_manifest_uri(suite_id, run_id),
        "config": run_config_uri(suite_id, run_id),
    }
