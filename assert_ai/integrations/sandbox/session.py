# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""ASSERT session that owns a stock sandbox around one test case."""
from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path
from typing import Any

from assert_ai.core.model_client import Message
from assert_ai.core.session import HTTPEndpointSession, TurnResult

from .mediation_setup import MediationSetup, load_setup
from .runtime import (
    ContainerSpec,
    ModelProxySpec,
    SandboxHandle,
    SandboxRuntimeError,
    egress_event,
    start_container,
)

log = logging.getLogger(__name__)


class SandboxedEndpointSession:
    """Start, use, and remove one configured sandbox for one ASSERT test case.

    ASSERT creates one runtime session per test case. Matching that lifetime gives
    each case a fresh process, filesystem, network, and mock state without relying
    on application-specific reset logic.
    """

    runtime_mode = "sandbox_container"

    def __init__(
        self,
        *,
        setup_path: str | Path,
        config_path: Path | None = None,
        message_timeout_s: float | None = None,
        startup_timeout_s: float | None = None,
    ) -> None:
        path = Path(setup_path).expanduser()
        if not path.is_absolute() and config_path is not None:
            path = config_path.parent / path
        self.setup: MediationSetup = load_setup(path.resolve())
        self._message_timeout_s = message_timeout_s
        self._startup_timeout_s = startup_timeout_s
        self._handle: SandboxHandle | None = None
        self._endpoint: HTTPEndpointSession | None = None
        self._workdir: tempfile.TemporaryDirectory[str] | None = None

    async def open(self) -> None:
        target = self.setup.target
        if target.kind == "endpoint":
            assert target.url
            self._endpoint = HTTPEndpointSession(
                endpoint=target.url,
                message_timeout_s=self._message_timeout_s,
            )
            await self._endpoint.open()
            return

        if self.setup.policy_path is None:
            raise SandboxRuntimeError("sandbox setup is missing its resolved policy path")
        proxy: ModelProxySpec | None = None
        if target.model_proxy:
            data = target.model_proxy
            upstream_url = str(data.get("upstream_url") or "").strip()
            credential_env = str(data.get("credential_env") or "").strip()
            if not upstream_url or not credential_env:
                raise SandboxRuntimeError(
                    "target.model_proxy requires upstream_url and credential_env"
                )
            proxy = ModelProxySpec(
                upstream_url=upstream_url,
                credential_env=credential_env,
                auth_style=str(data.get("auth_style") or "bearer"),
                model=str(data["model"]) if data.get("model") else None,
                container_base_url_env=str(data.get("container_base_url_env") or "OPENAI_BASE_URL"),
                container_key_env=str(data.get("container_key_env") or "OPENAI_API_KEY"),
            )

        self._workdir = tempfile.TemporaryDirectory(prefix="assert-sandbox-")
        timeout = self._startup_timeout_s or target.startup_timeout_s
        spec = ContainerSpec(
            image=str(target.image),
            container_port=int(target.port or 0),
            command=tuple(target.command),
            env=dict(target.env),
            health_path=target.health_path,
            endpoint_path=target.endpoint_path,
            startup_timeout_s=timeout,
            egress_allow_hosts=target.egress_allow_hosts,
            model_proxy=proxy,
            memory=target.memory,
            cpus=target.cpus,
            pids_limit=target.pids_limit,
            user=target.user,
        )
        try:
            self._handle = await asyncio.to_thread(
                start_container,
                spec,
                policy_path=self.setup.policy_path,
                mocks_path=self.setup.mocks_path,
                cassette_dir=self.setup.cassette_dir,
                output_dir=Path(self._workdir.name) / "output",
            )
            self._endpoint = HTTPEndpointSession(
                endpoint=self._handle.endpoint_url,
                message_timeout_s=self._message_timeout_s,
                allow_private=True,
            )
            await self._endpoint.open()
        except Exception:
            # `open` owns every resource it creates. A failure after Docker has
            # started (bad endpoint config, missing aiohttp, readiness race) must
            # not leak a container or network into the host. Preserve the primary
            # startup failure if cleanup also has a problem.
            try:
                await self.close()
            except Exception:  # noqa: BLE001
                log.exception("sandbox cleanup also failed after startup error")
            raise

    async def close(self) -> None:
        errors: list[Exception] = []
        if self._endpoint is not None:
            try:
                await self._endpoint.close()
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
            self._endpoint = None
        if self._handle is not None:
            try:
                await asyncio.to_thread(self._handle.stop)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
            self._handle = None
        if self._workdir is not None:
            self._workdir.cleanup()
            self._workdir = None
        if errors:
            raise errors[0]

    async def run_turn(self, messages: list[Message]) -> TurnResult:
        if self._endpoint is None:
            raise RuntimeError("sandbox session is not open")
        result = await self._endpoint.run_turn(messages)
        if self._handle is None:
            return result

        # Proxy-aware network attempts are written host-side, outside the
        # container. Add only rows produced since the previous turn.
        rows = await asyncio.to_thread(self._handle.new_egress_rows)
        if not rows:
            return result
        additions: list[dict[str, Any]] = []
        for row in rows:
            event = egress_event(row)
            additions.extend([
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "id": event["tool_call_id"],
                        "function": event["tool_name"],
                        "arguments": event["tool_args"],
                    }],
                    "raw": {"sandbox": "network_egress"},
                },
                {
                    "role": "tool",
                    "content": event["content"],
                    "function": event["tool_name"],
                    "arguments": event["tool_args"],
                    "tool_call_id": event["tool_call_id"],
                    "raw": {"sandbox": "network_egress"},
                },
            ])
        result.interaction_messages.extend(additions)
        return result

    @property
    def session_metadata(self) -> dict[str, Any]:
        target = self.setup.target
        metadata: dict[str, Any] = {
            "mode": self.runtime_mode if target.kind == "container" else "sandbox_endpoint",
            "target_kind": target.kind,
            "containment": (
                "read-only container + dropped capabilities + no-masquerade network + "
                "deny-by-default audited HTTP(S) proxy"
                if target.kind == "container"
                else "owned by the configured external endpoint"
            ),
            "raw_socket_audit": False,
        }
        if self._handle is not None:
            metadata["endpoint"] = self._handle.endpoint_url
        return metadata
