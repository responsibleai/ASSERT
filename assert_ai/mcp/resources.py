# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Lazy ASSERT MCP resources backed by inspect application services."""

from __future__ import annotations

import json
from typing import Any

import yaml
from mcp.server import MCPServer

from assert_ai.core.config_document import EVAL_CONFIG_SCHEMA_VERSION
from assert_ai.mcp.errors import invoke_resource
from assert_ai.mcp.sanitize import sanitize_for_mcp
from assert_ai.mcp.tools.inspect import InspectServices
from assert_ai.mcp.tools.jobs import JobServices
from assert_ai.services.errors import ServiceError, ServiceErrorCode

_SCHEMA_URI = "assert://schema/eval-config"


def register_inspect_resources(
    server: MCPServer,
    services: InspectServices,
    *,
    job_services: JobServices,
    inline_artifact_bytes: int,
) -> None:
    """Register static and templated resources for the inspect group."""

    workspace = services.workspace

    @server.resource(
        _SCHEMA_URI,
        name="eval-config-schema",
        title="ASSERT eval config schema",
        description="Canonical Draft 2020-12 JSON Schema for eval_config.yaml.",
        mime_type="application/json",
    )
    def eval_config_schema() -> str:
        return invoke_resource(
            lambda: _json_text(
                {
                    "schema_version": EVAL_CONFIG_SCHEMA_VERSION,
                    "json_schema": services.configs.get_schema(),
                },
                services=services,
            ),
            workspace=workspace,
        )

    @server.resource(
        "assert://preset/{kind}/{name}",
        name="preset",
        title="ASSERT preset",
        description="One built-in behavior or judge preset definition.",
        mime_type="application/json",
    )
    def preset(kind: str, name: str) -> str:
        return invoke_resource(
            lambda: _json_text(
                services.library.get_preset(kind, name).document,
                services=services,
            ),
            workspace=workspace,
        )

    @server.resource(
        "assert://config/{config_ref}",
        name="config",
        title="ASSERT managed config",
        description="One sanitized workspace-managed evaluation config.",
        mime_type="application/yaml",
    )
    def config(config_ref: str) -> str:
        return invoke_resource(
            lambda: _sanitized_config_yaml(config_ref, services=services),
            workspace=workspace,
        )

    @server.resource(
        "assert://job/{job_id}/log",
        name="job-log",
        title="ASSERT evaluation job log",
        description="Bounded, filtered stdout and stderr tails for one worker.",
        mime_type="text/plain",
    )
    def job_log(job_id: str) -> str:
        return invoke_resource(
            lambda: job_services.evaluations.read_log(
                job_id,
                max_bytes=inline_artifact_bytes,
            ),
            workspace=workspace,
        )

    @server.resource(
        "assert://suite/{suite_id}/taxonomy",
        name="suite-taxonomy",
        title="ASSERT suite taxonomy",
        description="The active behavior taxonomy for one result suite.",
        mime_type="application/json",
    )
    def suite_taxonomy(suite_id: str) -> str:
        return invoke_resource(
            lambda: _named_artifact_resource(
                suite_id,
                "taxonomy",
                services=services,
                inline_artifact_bytes=inline_artifact_bytes,
            ),
            workspace=workspace,
        )

    @server.resource(
        "assert://suite/{suite_id}/test-case/{test_case_id}{?kind,run_id}",
        name="suite-test-case",
        title="ASSERT test case",
        description="One complete active suite test case.",
        mime_type="application/json",
    )
    def suite_test_case(
        suite_id: str,
        test_case_id: str,
        kind: str | None = None,
        run_id: str | None = None,
    ) -> str:
        return invoke_resource(
            lambda: _json_text(
                services.results.get_test_case(
                    suite_id,
                    test_case_id,
                    kind=kind,
                    run_id=run_id,
                ),
                services=services,
            ),
            workspace=workspace,
        )

    @server.resource(
        "assert://run/{suite_id}/{run_id}/summary",
        name="run-summary",
        title="ASSERT run summary",
        description="Metadata-only quality, timing, usage, and model summary.",
        mime_type="application/json",
    )
    def run_summary(suite_id: str, run_id: str) -> str:
        return invoke_resource(
            lambda: _json_text(
                _public_run(
                    services.results.load_run_detail(suite_id, run_id)
                ),
                services=services,
            ),
            workspace=workspace,
        )

    @server.resource(
        "assert://run/{suite_id}/{run_id}/manifest",
        name="run-manifest",
        title="ASSERT run manifest",
        description="The persisted stage/status manifest for one run.",
        mime_type="application/json",
    )
    def run_manifest(suite_id: str, run_id: str) -> str:
        return invoke_resource(
            lambda: _named_artifact_resource(
                suite_id,
                "manifest",
                run_id=run_id,
                services=services,
                inline_artifact_bytes=inline_artifact_bytes,
            ),
            workspace=workspace,
        )

    @server.resource(
        "assert://run/{suite_id}/{run_id}/config",
        name="run-config",
        title="ASSERT run config",
        description="The sanitized immutable config snapshot used by one run.",
        mime_type="application/yaml",
    )
    def run_config(suite_id: str, run_id: str) -> str:
        return invoke_resource(
            lambda: _named_artifact_resource(
                suite_id,
                "config",
                run_id=run_id,
                services=services,
                inline_artifact_bytes=inline_artifact_bytes,
            ),
            workspace=workspace,
        )

    @server.resource(
        "assert://run/{suite_id}/{run_id}/transcript/{test_case_id}{?kind}",
        name="run-transcript",
        title="ASSERT run transcript",
        description="One inference transcript joined with its test case and score.",
        mime_type="application/json",
    )
    def run_transcript(
        suite_id: str,
        run_id: str,
        test_case_id: str,
        kind: str | None = None,
    ) -> str:
        return invoke_resource(
            lambda: _json_text(
                services.results.get_transcript(
                    suite_id,
                    run_id,
                    test_case_id,
                    kind=kind,
                ),
                services=services,
            ),
            workspace=workspace,
        )

    @server.resource(
        "assert://artifact/{artifact_id}",
        name="artifact",
        title="ASSERT artifact",
        description=(
            "One small redacted text artifact, or metadata directing the caller "
            "to read_artifact_chunk."
        ),
        mime_type="text/plain",
    )
    def artifact(artifact_id: str) -> str:
        return invoke_resource(
            lambda: _artifact_resource(
                artifact_id,
                services=services,
                inline_artifact_bytes=inline_artifact_bytes,
            ),
            workspace=workspace,
        )


def _sanitized_config_yaml(
    config_ref: str,
    *,
    services: InspectServices,
) -> str:
    record = services.configs.get_config(config_ref)
    document = sanitize_for_mcp(record.document, workspace=services.workspace)
    if not isinstance(document, dict):
        raise TypeError("Expected a config mapping")
    text = yaml.safe_dump(
        document,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    )
    normalized = text if text.endswith("\n") else text + "\n"
    return _bounded_text(normalized, services=services)


def _named_artifact_resource(
    suite_id: str,
    name: str,
    *,
    services: InspectServices,
    inline_artifact_bytes: int,
    run_id: str | None = None,
) -> str:
    descriptor = services.artifacts.find_artifact(
        suite_id,
        name,
        run_id=run_id,
    )
    return _artifact_resource(
        descriptor.artifact_id,
        services=services,
        inline_artifact_bytes=inline_artifact_bytes,
    )


def _artifact_resource(
    artifact_id: str,
    *,
    services: InspectServices,
    inline_artifact_bytes: int,
) -> str:
    descriptor = services.artifacts.get_artifact(artifact_id)
    if not descriptor.text:
        return _bounded_text(
            _artifact_redirect(
                descriptor.model_dump(mode="json"),
                readable=False,
                reason=(
                    "Binary artifact reads are disabled because their contents "
                    "cannot be safely redacted."
                ),
            ),
            services=services,
        )
    if descriptor.size_bytes > services.artifacts.max_text_artifact_bytes:
        return _bounded_text(
            _artifact_redirect(
                descriptor.model_dump(mode="json"),
                readable=False,
                reason=(
                    "This text artifact exceeds the generic read limit. "
                    "Use a dedicated paginated result tool when available."
                ),
            ),
            services=services,
        )
    if descriptor.size_bytes > inline_artifact_bytes:
        return _bounded_text(
            _artifact_redirect(
                descriptor.model_dump(mode="json"),
                readable=True,
            ),
            services=services,
        )
    chunk = services.artifacts.read_artifact_chunk(
        artifact_id,
        chunk_size=max(4, descriptor.size_bytes),
    )
    if not chunk.eof:
        return _bounded_text(
            _artifact_redirect(
                descriptor.model_dump(mode="json"),
                readable=True,
            ),
            services=services,
        )
    return _bounded_text(chunk.data, services=services)


def _artifact_redirect(
    descriptor: dict[str, Any],
    *,
    readable: bool,
    reason: str | None = None,
) -> str:
    payload: dict[str, Any] = {
        "artifact": descriptor,
        "inline": False,
        "readable": readable,
    }
    if readable:
        payload["next_step"] = (
            "Call read_artifact_chunk with artifact.artifact_id for bounded access."
        )
    if reason is not None:
        payload["reason"] = reason
    return json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )


def _json_text(value: Any, *, services: InspectServices) -> str:
    return _bounded_text(
        json.dumps(
            sanitize_for_mcp(value, workspace=services.workspace),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        services=services,
    )


def _public_run(summary: dict[str, Any]) -> dict[str, Any]:
    payload = dict(summary)
    for key in ("artifact_versions", "sources", "indexes"):
        payload.pop(key, None)
    return payload


def _bounded_text(text: str, *, services: InspectServices) -> str:
    size_bytes = len(text.encode("utf-8"))
    if size_bytes > services.max_response_bytes:
        raise ServiceError(
            ServiceErrorCode.ARTIFACT_TOO_LARGE,
            (
                "Resource exceeds the configured response limit; "
                "use its paginated or chunked tool"
            ),
            details={
                "size_bytes": size_bytes,
                "max_response_bytes": services.max_response_bytes,
            },
        )
    return text
