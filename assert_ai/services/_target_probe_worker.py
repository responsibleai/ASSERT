# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Internal subprocess entry point for isolated target probing."""

from __future__ import annotations

import inspect
import io
import json
import sys
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from assert_ai.config import load_runtime_context
from assert_ai.core.security import (
    redact_path_prefixes,
    sanitize_text,
    validate_callable_ref,
    validate_module_ref,
)
from assert_ai.core.session import _discover_connector_class
from assert_ai.core.tool_backend import (
    import_callable_module,
    inspect_tool_module,
    load_tool_module,
)
from assert_ai.core.workspace import WorkspaceService
from assert_ai.services.configs import ConfigService
from assert_ai.stages import STAGES

_RESULT_MARKER = "ASSERT_TARGET_PROBE_RESULT="
_MAX_REQUEST_BYTES = 1024 * 1024


class _DiscardText(io.TextIOBase):
    def write(self, value: str) -> int:
        return len(value)

    def writable(self) -> bool:
        return True


def main() -> int:
    workspace: WorkspaceService | None = None
    result_token = ""
    payload: dict[str, Any]
    try:
        raw = sys.stdin.buffer.read(_MAX_REQUEST_BYTES + 1)
        if len(raw) > _MAX_REQUEST_BYTES:
            raise ValueError("Target probe request exceeds the worker limit")
        request = json.loads(raw.decode("utf-8"))
        if not isinstance(request, dict):
            raise ValueError("Target probe request must be an object")
        result_token = _required_string(request, "result_token")
        workspace = WorkspaceService.create(
            _required_string(request, "workspace_root")
        )
        with (
            redirect_stdout(_DiscardText()),
            redirect_stderr(_DiscardText()),
        ):
            payload = _probe(workspace, request)
    except Exception as exc:  # noqa: BLE001 - process boundary returns failure
        message = sanitize_text(str(exc))
        if workspace is not None:
            message = redact_path_prefixes(
                message,
                (
                    workspace.root,
                    workspace.configs_root,
                    workspace.artifacts_root,
                    workspace.results_root,
                ),
            )
        payload = {
            "ok": False,
            "error_type": type(exc).__name__,
            "message": message or "Target probe failed",
        }
    finally:
        _cleanup_descendants()

    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    sys.__stdout__.write(
        f"{_RESULT_MARKER}{result_token}={encoded}\n"
    )
    sys.__stdout__.flush()
    return 0 if payload.get("ok") is True else 1


def _probe(
    workspace: WorkspaceService,
    request: dict[str, Any],
) -> dict[str, Any]:
    config_ref = _required_string(request, "config_ref")
    max_config_bytes = request.get("max_config_bytes")
    if (
        not isinstance(max_config_bytes, int)
        or isinstance(max_config_bytes, bool)
        or max_config_bytes < 1
    ):
        raise ValueError("max_config_bytes must be a positive integer")
    configs = ConfigService(
        workspace,
        max_config_bytes=max_config_bytes,
    )
    record = configs.get_config(config_ref)
    if not record.validation.valid:
        raise ValueError("Target probe requires a valid config")
    config_path = workspace.path_policy.resolve_config_path(
        record.config_ref,
        must_exist=True,
        reject_links=True,
    )
    ctx = load_runtime_context(
        deepcopy(record.document),
        config_path,
        stage_modules=STAGES,
        path_policy=workspace.path_policy,
    )
    target = ctx.get("target")
    inference_enabled = any(
        stage_name == "inference" and raw_cfg.get("enabled", True)
        for stage_name, raw_cfg in ctx["stages"]
    )
    if not inference_enabled or target is None:
        raise ValueError(
            "Config has no enabled inference target to probe"
        )

    if target.model is not None:
        details: dict[str, Any] = {
            "model": str(target.model.name),
            "trace_enabled": target.trace is not None,
        }
        tools = target.tools
        if tools is not None and tools.module:
            tools_class, schemas = inspect_tool_module(
                tools.module,
                config_path=config_path,
                path_policy=workspace.path_policy,
            )
            details["tools_module"] = tools.module
            details["tools_class"] = tools_class.__name__
            details["tool_count"] = len(schemas)
        if tools is not None and tools.toolset:
            toolset_path = workspace.path_policy.resolve_input(
                tools.toolset,
                base_dir=config_path.parent,
                field_name="pipeline.inference.target.tools.toolset",
                must_exist=True,
                file_only=True,
            )
            details["toolset"] = workspace.reference(toolset_path)
        return {
            "ok": True,
            "target_kind": "model",
            "details": details,
        }

    if target.callable:
        validate_callable_ref(target.callable)
        module_ref, function_name = target.callable.rsplit(":", 1)
        module = import_callable_module(
            module_ref,
            config_path=config_path,
            path_policy=workspace.path_policy,
        )
        try:
            function = getattr(module, function_name)
        except AttributeError as exc:
            raise ValueError(
                f"Module {module_ref!r} has no attribute {function_name!r}"
            ) from exc
        if not callable(function):
            raise ValueError(
                f"Target attribute {target.callable!r} is not callable"
            )
        signature = inspect.signature(function)
        return {
            "ok": True,
            "target_kind": "callable",
            "details": {
                "reference": target.callable,
                "is_async": inspect.iscoroutinefunction(function),
                "accepts_history": "history" in signature.parameters,
                "parameters": tuple(signature.parameters)[:20],
                "trace_enabled": target.trace is not None,
            },
        }

    if target.connector:
        validate_module_ref(target.connector)
        module = load_tool_module(
            target.connector,
            config_path=config_path,
            path_policy=workspace.path_policy,
        )
        connector_class = _discover_connector_class(module)
        return {
            "ok": True,
            "target_kind": "connector",
            "details": {
                "reference": target.connector,
                "adapter_class": connector_class.__name__,
            },
        }

    if target.sandbox:
        from assert_ai.integrations.sandbox import load_setup

        setup = load_setup(
            target.sandbox,
            path_policy=workspace.path_policy,
        )
        if setup.target.kind == "endpoint":
            from assert_ai.core.session import HTTPEndpointSession

            HTTPEndpointSession(endpoint=str(setup.target.url or ""))
        return {
            "ok": True,
            "target_kind": "sandbox",
            "details": {
                "setup": workspace.reference(setup.source_path),
                "runtime_kind": setup.target.kind,
                "policy": workspace.reference(setup.policy_path),
                "mocks_configured": setup.mocks_path is not None,
                "cassettes_configured": setup.cassette_dir is not None,
            },
        }

    endpoint = str(target.endpoint or "")
    from assert_ai.core.session import HTTPEndpointSession

    HTTPEndpointSession(endpoint=endpoint)
    return {
        "ok": True,
        "target_kind": "endpoint",
        "details": {"origin": _endpoint_origin(endpoint)},
    }


def _endpoint_origin(value: str) -> str:
    parsed = urlsplit(value)
    if not parsed.scheme or not parsed.hostname:
        raise ValueError("Endpoint target is invalid")
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme, host, "", "", ""))


def _required_string(request: dict[str, Any], key: str) -> str:
    value = request.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _cleanup_descendants() -> None:
    try:
        import psutil
    except ImportError:
        return
    try:
        children = psutil.Process().children(recursive=True)
        for child in children:
            child.terminate()
        _, alive = psutil.wait_procs(children, timeout=1)
        for child in alive:
            child.kill()
        psutil.wait_procs(alive, timeout=1)
    except psutil.Error:
        return


if __name__ == "__main__":
    raise SystemExit(main())
