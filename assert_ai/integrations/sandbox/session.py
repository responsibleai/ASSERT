# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""ASSERT session that owns a stock sandbox around one test case."""
from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path
from typing import Any, cast

from assert_ai.core.action_claims import ActionClaim, make_action_claim
from assert_ai.core.model_client import Message
from assert_ai.core.session import HTTPEndpointSession, TurnResult

from .evidence import host_action_event
from .host_mediator import HostActionBatch
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
_MAX_SANDBOX_RESPONSE_BYTES = 4 * 1024 * 1024


def _validate_target_action_case(
    interaction_messages: list[dict[str, Any]],
    expected_case_id: str | None,
) -> None:
    """Reject target-reported mediation evidence for another or unknown case."""
    if not expected_case_id:
        return
    for message in interaction_messages:
        raw = message.get("raw")
        if not isinstance(raw, dict):
            continue
        evidence = raw.get("action_mediation")
        if not isinstance(evidence, dict):
            continue
        reported = evidence.get("case_id")
        if not isinstance(reported, str) or not reported.strip():
            raise RuntimeError(
                "target action-mediation evidence is missing the ASSERT case_id"
            )
        if reported.strip() != expected_case_id:
            raise RuntimeError(
                "target action-mediation evidence case_id does not match the "
                "ASSERT-owned sandbox case"
            )


def _target_claims_from_messages(
    messages: list[dict[str, Any]],
) -> list[ActionClaim]:
    """Compatibility extraction for test doubles without private claims."""
    claims: list[ActionClaim] = []
    event_index = 0
    for message in messages:
        tool_calls = message.get("tool_calls")
        if tool_calls:
            if not isinstance(tool_calls, list):
                raise RuntimeError("target tool_calls must be a list")
            for call in tool_calls:
                event_index += 1
                if not isinstance(call, dict):
                    raise RuntimeError("target tool call must be an object")
                try:
                    claims.append(make_action_claim(
                        kind="call",
                        call_id=call.get("id"),
                        tool=call.get("function"),
                        arguments=call.get("arguments", {}),
                        arguments_supplied=True,
                    ))
                except ValueError as exc:
                    raise RuntimeError(
                        f"target action event {event_index} is invalid: {exc}"
                    ) from exc
        if message.get("role") == "tool":
            event_index += 1
            try:
                claims.append(make_action_claim(
                    kind="result",
                    call_id=message.get("tool_call_id"),
                    tool=message.get("function"),
                    arguments=message.get("arguments"),
                    arguments_supplied="arguments" in message,
                ))
            except ValueError as exc:
                raise RuntimeError(
                    f"target action event {event_index} is invalid: {exc}"
                ) from exc
    return claims


def _host_claims_from_rows(rows: list[dict[str, Any]]) -> list[ActionClaim]:
    claims: list[ActionClaim] = []
    for index, row in enumerate(rows, start=1):
        try:
            claims.append(make_action_claim(
                kind="call",
                call_id=row.get("id"),
                tool=row.get("tool"),
                arguments=row.get("args"),
                arguments_supplied=True,
            ))
        except ValueError as exc:
            raise RuntimeError(
                f"host action row {index} is invalid: {exc}"
            ) from exc
    return claims


def _reconcile_action_claims(
    target_claims: list[ActionClaim],
    host_claims: list[ActionClaim],
    host_public_ids: list[str],
) -> list[tuple[str, str]]:
    """Match each target action occurrence to one private host claim."""
    if len(host_claims) != len(host_public_ids):
        raise RuntimeError("host action claim and evidence counts disagree")

    host_by_id: dict[str, tuple[ActionClaim, str, int]] = {}
    for index, (claim, public_id) in enumerate(
        zip(host_claims, host_public_ids, strict=True),
        start=1,
    ):
        if claim.kind != "call":
            raise RuntimeError(f"host action claim {index} is not a call")
        if claim.call_id_digest in host_by_id:
            raise RuntimeError("host action ledger returned a duplicate call ID")
        host_by_id[claim.call_id_digest] = (claim, public_id, index)

    target_actions: dict[str, ActionClaim] = {}
    target_action_ids: list[str] = []
    target_call_order: list[str] = []
    result_ids: set[str] = set()
    for index, claim in enumerate(target_claims, start=1):
        call_id = claim.call_id_digest
        if claim.kind == "call":
            if call_id in target_actions:
                raise RuntimeError(
                    f"duplicate target tool-call occurrence at action event {index}"
                )
            target_actions[call_id] = claim
            target_action_ids.append(call_id)
            target_call_order.append(call_id)
            continue

        if call_id in result_ids:
            raise RuntimeError(
                f"duplicate target tool-result occurrence at action event {index}"
            )
        result_ids.add(call_id)
        existing = target_actions.get(call_id)
        if existing is None:
            if not claim.arguments_supplied:
                raise RuntimeError(
                    f"target tool result at action event {index} omits arguments "
                    "without a matching tool call"
                )
            target_actions[call_id] = ActionClaim(
                kind="call",
                call_id_digest=claim.call_id_digest,
                tool_digest=claim.tool_digest,
                arguments_digest=claim.arguments_digest,
                arguments_supplied=True,
            )
            target_action_ids.append(call_id)
            continue
        if existing.tool_digest != claim.tool_digest or (
            claim.arguments_supplied
            and existing.arguments_digest != claim.arguments_digest
        ):
            raise RuntimeError(
                f"target action event {index} conflicts with its matching tool call"
            )

    missing_positions: list[str] = []
    mismatch_positions: list[str] = []
    reordered_positions: list[str] = []
    for position, call_id in enumerate(target_action_ids, start=1):
        host_match = host_by_id.get(call_id)
        if host_match is None:
            missing_positions.append(str(position))
            continue
        target_claim = target_actions[call_id]
        host_claim, _public_id, host_position = host_match
        if (
            target_claim.tool_digest != host_claim.tool_digest
            or target_claim.arguments_digest != host_claim.arguments_digest
        ):
            mismatch_positions.append(str(position))

    last_host_position = 0
    for position, call_id in enumerate(target_call_order, start=1):
        host_match = host_by_id.get(call_id)
        if host_match is None:
            continue
        _host_claim, _public_id, host_position = host_match
        if host_position <= last_host_position:
            reordered_positions.append(str(position))
        last_host_position = host_position

    if missing_positions or mismatch_positions or reordered_positions:
        details: list[str] = []
        if missing_positions:
            details.append(
                "missing host action occurrences: " + ", ".join(missing_positions)
            )
        if mismatch_positions:
            details.append(
                "host/target identity mismatch at action occurrences: "
                + ", ".join(mismatch_positions)
            )
        if reordered_positions:
            details.append(
                "target action order diverges from host sequence at occurrences: "
                + ", ".join(reordered_positions)
            )
        raise RuntimeError(
            "host action mediation is enabled, but the target returned action events "
            "not accounted for by the host mediator (" + "; ".join(details) + ")"
        )

    return [
        (claim.kind, host_by_id[claim.call_id_digest][1])
        for claim in target_claims
    ]


def _replace_target_actions_with_host_evidence(
    messages: list[dict[str, Any]],
    additions: list[dict[str, Any]],
    event_public_ids: list[tuple[str, str]],
    host_public_ids: set[str],
) -> list[dict[str, Any]]:
    """Replace target action events in place using private reconciliation order."""
    host_messages: dict[str, dict[str, dict[str, Any]]] = {}
    host_order: list[str] = []
    passthrough: list[dict[str, Any]] = []
    for message in additions:
        call_id: Any = None
        kind: str | None = None
        tool_calls = message.get("tool_calls")
        if message.get("role") == "assistant" and isinstance(tool_calls, list) and tool_calls:
            call = tool_calls[0]
            if isinstance(call, dict):
                call_id = call.get("id")
                kind = "call"
        elif message.get("role") == "tool":
            call_id = message.get("tool_call_id")
            kind = "result"
        if isinstance(call_id, str) and call_id in host_public_ids and kind is not None:
            if call_id not in host_messages:
                host_messages[call_id] = {}
                host_order.append(call_id)
            host_messages[call_id][kind] = message
        else:
            passthrough.append(message)

    event_index = 0
    target_result_ids = {
        public_id
        for kind, public_id in event_public_ids
        if kind == "result"
    }

    def next_public_id(expected_kind: str) -> str:
        nonlocal event_index
        if event_index >= len(event_public_ids):
            raise RuntimeError("target action layout contains more events than private claims")
        kind, public_id = event_public_ids[event_index]
        event_index += 1
        if kind != expected_kind:
            raise RuntimeError("target action layout disagrees with private claim order")
        return public_id

    rebuilt: list[dict[str, Any]] = []
    inserted: set[tuple[str, str]] = set()
    for message in messages:
        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            if message.get("content"):
                prose = dict(message)
                prose.pop("tool_calls", None)
                rebuilt.append(prose)
            for _call in tool_calls:
                public_id = next_public_id("call")
                host_call = host_messages.get(public_id, {}).get("call")
                if host_call is None:
                    raise RuntimeError("matched host action is missing rendered call evidence")
                if (public_id, "call") not in inserted:
                    rebuilt.append(host_call)
                    inserted.add((public_id, "call"))
                if public_id not in target_result_ids:
                    host_result = host_messages[public_id].get("result")
                    if host_result is None:
                        raise RuntimeError(
                            "matched host action is missing rendered result evidence"
                        )
                    if (public_id, "result") not in inserted:
                        rebuilt.append(host_result)
                        inserted.add((public_id, "result"))
            continue
        if message.get("role") == "tool":
            public_id = next_public_id("result")
            host_call = host_messages.get(public_id, {}).get("call")
            if host_call is None:
                raise RuntimeError("matched host action is missing rendered call evidence")
            if (public_id, "call") not in inserted:
                rebuilt.append(host_call)
                inserted.add((public_id, "call"))
            host_result = host_messages[public_id].get("result")
            if host_result is None:
                raise RuntimeError("matched host action is missing rendered result evidence")
            if (public_id, "result") not in inserted:
                rebuilt.append(host_result)
                inserted.add((public_id, "result"))
            continue
        rebuilt.append(message)

    if event_index != len(event_public_ids):
        raise RuntimeError("private action claims contain events missing from the transcript")

    remaining: list[dict[str, Any]] = []
    for call_id in host_order:
        for kind in ("call", "result"):
            host_message = host_messages[call_id].get(kind)
            if host_message is not None and (call_id, kind) not in inserted:
                remaining.append(host_message)
    remaining.extend(passthrough)
    if not remaining:
        return rebuilt
    final_assistant = [
        index
        for index, message in enumerate(rebuilt)
        if message.get("role") == "assistant" and message.get("content")
    ]
    insert_at = final_assistant[-1] if final_assistant else len(rebuilt)
    return [*rebuilt[:insert_at], *remaining, *rebuilt[insert_at:]]


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
        case_id: str | None = None,
        config_path: Path | None = None,
        message_timeout_s: float | None = None,
        startup_timeout_s: float | None = None,
    ) -> None:
        path = Path(setup_path).expanduser()
        if not path.is_absolute() and config_path is not None:
            path = config_path.parent / path
        self.setup: MediationSetup = load_setup(path.resolve())
        self.case_id = case_id
        self._message_timeout_s = message_timeout_s
        self._startup_timeout_s = startup_timeout_s
        self._handle: SandboxHandle | None = None
        self._endpoint: HTTPEndpointSession | None = None
        self._workdir: tempfile.TemporaryDirectory[str] | None = None
        self._buffered_interaction_messages: list[dict[str, Any]] = []
        self._drained_host_action_rows = 0
        self._drained_host_action_claims: list[ActionClaim] = []
        self._drained_host_action_public_ids: list[str] = []

    async def open(self) -> None:
        target = self.setup.target
        if target.kind == "endpoint":
            assert target.url
            self._endpoint = HTTPEndpointSession(
                endpoint=target.url,
                message_timeout_s=self._message_timeout_s,
                case_id=self.case_id,
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
            case_id=self.case_id,
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
            host_action_mediation=target.host_action_mediation,
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
                case_id=self.case_id,
                capture_action_claims=target.host_action_mediation,
                max_response_bytes=_MAX_SANDBOX_RESPONSE_BYTES,
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
            try:
                final_messages = await self.drain_pending_interaction_messages(
                    finalize_pending=True,
                )
                self._buffered_interaction_messages.extend(final_messages)
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
        host_mediated = (
            self._handle is not None
            and getattr(self._handle, "action_ledger", None) is not None
        )
        additions = await self.drain_pending_interaction_messages()
        if host_mediated:
            try:
                target_action_claims = (
                    result._action_claims
                    if result._action_claims is not None
                    else _target_claims_from_messages(result.interaction_messages)
                )
                event_public_ids = _reconcile_action_claims(
                    target_action_claims,
                    self._drained_host_action_claims,
                    self._drained_host_action_public_ids,
                )
                # The evaluated target controls endpoint events. Replace each
                # action at its original position using the private match order.
                # Host-only rows remain visible before the final response.
                result.interaction_messages = _replace_target_actions_with_host_evidence(
                    result.interaction_messages,
                    additions,
                    event_public_ids,
                    set(self._drained_host_action_public_ids),
                )
            except RuntimeError:
                self._buffered_interaction_messages.extend(additions)
                raise
        else:
            try:
                _validate_target_action_case(result.interaction_messages, self.case_id)
            except RuntimeError:
                self._buffered_interaction_messages.extend(additions)
                raise
            result.interaction_messages.extend(additions)
        return result

    async def drain_pending_interaction_messages(
        self,
        *,
        finalize_pending: bool = False,
    ) -> list[dict[str, Any]]:
        """Drain host evidence without finalizing active calls before shutdown."""
        if self._handle is None:
            buffered, self._buffered_interaction_messages = (
                self._buffered_interaction_messages,
                [],
            )
            return buffered
        buffered = list(self._buffered_interaction_messages)
        new_action_batch = getattr(
            self._handle,
            (
                "new_action_batch"
                if finalize_pending
                else "new_ready_action_batch"
            ),
            None,
        )
        if not callable(new_action_batch):
            new_action_batch = getattr(self._handle, "new_action_batch", None)
        action_rows: list[dict[str, Any]] = []
        action_claims: list[ActionClaim] = []
        if callable(new_action_batch):
            batch = cast(
                HostActionBatch,
                await asyncio.to_thread(new_action_batch),
            )
            action_rows = batch.rows
            action_claims = batch.claims
        else:
            new_action_rows = getattr(self._handle, "new_action_rows", None)
            if callable(new_action_rows):
                action_rows = cast(
                    list[dict[str, Any]],
                    await asyncio.to_thread(new_action_rows),
                )
                action_claims = _host_claims_from_rows(action_rows)
        host_public_ids = [
            str(row.get("id") or "")
            for row in action_rows
        ]
        if any(not public_id for public_id in host_public_ids):
            raise RuntimeError("host action evidence is missing a public call ID")
        new_egress_rows = getattr(self._handle, "new_egress_rows", None)
        egress_rows = (
            cast(
                list[dict[str, Any]],
                await asyncio.to_thread(new_egress_rows),
            )
            if callable(new_egress_rows)
            else []
        )
        self._drained_host_action_rows = len(action_rows)
        self._drained_host_action_claims = action_claims
        self._drained_host_action_public_ids = host_public_ids
        events = [host_action_event(row) for row in action_rows]
        events.extend(egress_event(row, case_id=self.case_id) for row in egress_rows)
        additions: list[dict[str, Any]] = []
        for event in events:
            raw = event.get("raw") or {"sandbox": "host_evidence"}
            additions.extend([
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "id": event["tool_call_id"],
                        "function": event["tool_name"],
                        "arguments": event["tool_args"],
                    }],
                    "raw": raw,
                },
                {
                    "role": "tool",
                    "content": event["content"],
                    "function": event["tool_name"],
                    "arguments": event["tool_args"],
                    "tool_call_id": event["tool_call_id"],
                    "raw": raw,
                },
            ])
        self._buffered_interaction_messages = []
        return [*buffered, *additions]

    @property
    def preserve_error_transcript(self) -> bool:
        """Container failures retain evidence; external endpoints keep legacy propagation."""
        return self.setup.target.kind == "container"

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
        if target.kind == "container":
            metadata["action_evidence"] = (
                "host-authoritative attempts and decisions; host-authoritative mock/block results; "
                "target-reported pass results"
                if target.host_action_mediation
                else "target-reported"
            )
        if self.case_id:
            metadata["case_id"] = self.case_id
        if self._handle is not None:
            metadata["endpoint"] = self._handle.endpoint_url
        return metadata
