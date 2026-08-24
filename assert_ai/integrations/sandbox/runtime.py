# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Stock Docker containment for sandboxed ASSERT targets.

The mediation layer controls declared tool calls. This module supplies the
complementary process and network boundary for everything else:

* one disposable container per ASSERT test case;
* read-only root filesystem, dropped Linux capabilities, and no-new-privileges;
* a private Docker network with no gateway, so neither the host nor public
  internet is directly reachable;
* a deny-by-default HTTP(S) proxy that records every proxy-aware egress attempt;
* policy and mock files mounted read-only, with a separate writable target-output
  mount and a host-only network-evidence ledger;
* optional host-side model proxy, so a real provider credential never enters the
  container.

The untrusted target sees only a narrow trusted relay for endpoint ingress,
audited egress, and optional model traffic. Raw sockets or clients that ignore
HTTP_PROXY are still blocked by the Docker network but cannot be attributed in
the HTTP audit ledger. That boundary is reported explicitly in runtime metadata.
"""
from __future__ import annotations

import base64
import http.client
import ipaddress
import json
import logging
import os
import secrets
import select
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from assert_ai.core.security import validate_endpoint_url

log = logging.getLogger(__name__)

_RELAY_IMAGE = "python:3.11-slim"
_RELAY_ALIAS = "assert-sandbox-relay"
_TARGET_ALIAS = "assert-sandbox-target"
_RELAY_TARGET_PORT = 18080
_RELAY_EGRESS_PORT = 18081
_RELAY_MODEL_PORT = 18082


class SandboxRuntimeError(RuntimeError):
    """Raised when the stock sandbox cannot be started or stopped safely."""


def _safe_content_type(value: str | None, default: str) -> str:
    """Return a header-safe media type without forwarding response delimiters.

    Both proxy upstreams are outside ASSERT's trust boundary.  Python's
    ``send_header`` does not reject embedded CR/LF, so forwarding an upstream
    ``Content-Type`` verbatim would let a malicious service append response
    headers.  Preserve ordinary media types, but fail closed to the caller's
    fixed default for response splitting, other control bytes, or values that
    cannot be emitted by ``BaseHTTPRequestHandler``.
    """
    candidate = str(value or default).strip()
    if not candidate or "\r" in candidate or "\n" in candidate:
        return default
    if any(ord(char) < 32 or ord(char) == 127 for char in candidate):
        return default
    try:
        candidate.encode("latin-1")
    except UnicodeEncodeError:
        return default
    return candidate


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["docker", *args],
            check=check,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise SandboxRuntimeError(
            "Docker is required for target.sandbox container targets but the "
            "`docker` command was not found. Install Docker, or use an endpoint "
            "target for an already-running sandbox."
        ) from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        raise SandboxRuntimeError(f"docker {' '.join(args[:2])} failed: {detail}") from exc


def _cleanup_docker(commands: tuple[tuple[str, ...], ...]) -> list[Exception]:
    """Attempt every Docker cleanup and report exceptions plus nonzero exits."""
    errors: list[Exception] = []
    for command in commands:
        try:
            result = _docker(*command, check=False)
        except Exception as exc:  # noqa: BLE001 - keep releasing later resources
            errors.append(exc)
            continue
        if getattr(result, "returncode", 0) != 0:
            detail = (getattr(result, "stderr", "") or getattr(result, "stdout", "") or "").strip()
            suffix = f": {detail}" if detail else ""
            errors.append(
                SandboxRuntimeError(
                    f"docker {' '.join(command[:2])} cleanup exited "
                    f"{result.returncode}{suffix}"
                )
            )
    return errors


def docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    result = subprocess.run(
        ["docker", "info", "--format", "{{.ServerVersion}}"],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _wait_http(url: str, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    last_error = "not ready"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:  # noqa: S310 - local health probe
                if 200 <= response.status < 500:
                    return
        except Exception as exc:  # noqa: BLE001 - retry until the deadline
            last_error = str(exc)
        time.sleep(0.25)
    raise SandboxRuntimeError(f"sandbox did not become ready at {url}: {last_error}")


def _read_jsonl(path: Path, start: int = 0) -> tuple[list[dict[str, Any]], int]:
    if not path.exists():
        return [], start
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    rows: list[dict[str, Any]] = []
    for line in lines[start:]:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows, len(lines)


def _resolve_public_ip(host: str) -> str:
    """Resolve once and return a globally routable address.

    Connecting to this returned address, rather than resolving the hostname a
    second time in the HTTP client, closes the DNS-rebinding window between
    validation and connection.
    """
    try:
        addresses = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"cannot resolve egress host {host!r}") from exc
    for _family, _kind, _proto, _canonname, sockaddr in addresses:
        address = ipaddress.ip_address(sockaddr[0])
        if address.is_global:
            return str(address)
    raise ValueError(f"egress host {host!r} does not resolve to a public address")


# ---------------------------------------------------------------------------
# Deny-by-default egress audit proxy
# ---------------------------------------------------------------------------


class _EgressHandler(BaseHTTPRequestHandler):
    allow_hosts: frozenset[str] = frozenset()
    audit_log: Path = Path("egress.jsonl")
    proxy_token: str = ""
    _lock = threading.Lock()

    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return None

    def _record(self, host: str, port: int, method: str, path: str, decision: str) -> None:
        row = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "channel": "egress",
            "host": host,
            "port": port,
            "method": method,
            "path": path,
            "decision": decision,
        }
        with type(self)._lock:
            self.audit_log.parent.mkdir(parents=True, exist_ok=True)
            with self.audit_log.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _authorized(self) -> bool:
        expected = "Basic " + base64.b64encode(
            f"assert:{self.proxy_token}".encode()
        ).decode()
        if self.headers.get("proxy-authorization") == expected:
            return True
        self.send_response(407, "Proxy Authentication Required")
        self.send_header("proxy-authenticate", 'Basic realm="assert-sandbox"')
        self.send_header("content-length", "0")
        self.send_header("connection", "close")
        self.end_headers()
        return False

    def _deny(self, host: str, port: int, method: str, path: str) -> None:
        self._record(host, port, method, path, "denied")
        body = json.dumps({
            "error": "egress_denied",
            "detail": f"{host}:{port} is not in the sandbox egress allow-list",
        }).encode()
        self.send_response(403)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.send_header("connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _forward(self, method: str) -> None:
        if not self._authorized():
            return
        parsed = urlparse(self.path)
        host = parsed.hostname or ""
        port = parsed.port or 80
        path = parsed.path or "/"
        if not host or host not in self.allow_hosts:
            self._deny(host, port, method, path)
            return
        try:
            # Exact-host policy is necessary but not sufficient: also block
            # loopback, private/link-local ranges, and metadata endpoints before
            # the host process makes the request.
            validate_endpoint_url(self.path)
            resolved_ip = _resolve_public_ip(host)
        except ValueError:
            self._deny(host, port, method, path)
            return
        if parsed.scheme != "http":
            self._deny(host, port, method, path)
            return
        self._record(host, port, method, path, "allowed")
        length = int(self.headers.get("content-length", 0) or 0)
        body = self.rfile.read(length) if length else None
        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in {
                "proxy-authorization", "proxy-connection", "connection", "keep-alive"
            }
        }
        request_path = path
        if parsed.query:
            request_path += f"?{parsed.query}"
        headers["host"] = host if port == 80 else f"{host}:{port}"
        connection = http.client.HTTPConnection(resolved_ip, port, timeout=30)
        try:
            connection.request(method, request_path, body=body, headers=headers)
            response = connection.getresponse()
            raw = response.read()
            status = response.status
            content_type = response.headers.get("content-type", "application/octet-stream")
        except Exception as exc:  # noqa: BLE001
            raw = json.dumps({"error": "upstream_failed", "detail": str(exc)}).encode()
            status = 502
            content_type = "application/json"
        finally:
            connection.close()
        self.send_response(status)
        self.send_header(
            "content-type",
            _safe_content_type(content_type, "application/octet-stream"),
        )
        self.send_header("content-length", str(len(raw)))
        self.send_header("connection", "close")
        self.end_headers()
        self.wfile.write(raw)

    do_GET = lambda self: self._forward("GET")  # noqa: N815,E731
    do_POST = lambda self: self._forward("POST")  # noqa: N815,E731
    do_PUT = lambda self: self._forward("PUT")  # noqa: N815,E731
    do_DELETE = lambda self: self._forward("DELETE")  # noqa: N815,E731

    def do_CONNECT(self) -> None:  # noqa: N802
        if not self._authorized():
            return
        host, _, port_text = self.path.partition(":")
        port = int(port_text or 443)
        if not host or host not in self.allow_hosts:
            self._deny(host, port, "CONNECT", "")
            return
        try:
            validate_endpoint_url(f"https://{host}:{port}/")
            resolved_ip = _resolve_public_ip(host)
        except ValueError:
            self._deny(host, port, "CONNECT", "")
            return
        self._record(host, port, "CONNECT", "", "allowed")
        try:
            upstream = socket.create_connection((resolved_ip, port), timeout=30)
        except OSError:
            self.send_response(502)
            self.send_header("connection", "close")
            self.end_headers()
            return
        self.send_response(200, "Connection Established")
        self.end_headers()
        sockets = [self.connection, upstream]
        try:
            while True:
                readable, _, exceptional = select.select(sockets, [], sockets, 30)
                if exceptional or not readable:
                    break
                for source in readable:
                    destination = upstream if source is self.connection else self.connection
                    data = source.recv(65536)
                    if not data:
                        return
                    destination.sendall(data)
        except OSError:
            pass
        finally:
            upstream.close()


def _start_egress_proxy(
    *, audit_log: Path, allow_hosts: tuple[str, ...], proxy_token: str
) -> tuple[ThreadingHTTPServer, threading.Thread, int]:
    port = _free_port()
    handler = type(
        "SandboxEgressHandler",
        (_EgressHandler,),
        {
            "allow_hosts": frozenset(allow_hosts),
            "audit_log": audit_log,
            "proxy_token": proxy_token,
        },
    )
    server = ThreadingHTTPServer(("0.0.0.0", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, port


# ---------------------------------------------------------------------------
# Optional host-side model credential proxy
# ---------------------------------------------------------------------------


class _ModelProxyHandler(BaseHTTPRequestHandler):
    upstream_url = ""
    credential = ""
    auth_style = "bearer"
    model: str | None = None
    access_token: str = ""

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return None

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._send(200, b'{"status":"ok"}')
        else:
            self._send(404, b'{"error":"not found"}')

    def do_POST(self) -> None:  # noqa: N802
        if self.headers.get("authorization") != f"Bearer {self.access_token}":
            self._send(401, b'{"error":"invalid sandbox proxy token"}')
            return
        length = int(self.headers.get("content-length", 0) or 0)
        body = self.rfile.read(length) if length else b"{}"
        if self.model:
            try:
                payload = json.loads(body)
                payload["model"] = self.model
                body = json.dumps(payload).encode()
            except (json.JSONDecodeError, TypeError):
                pass
        headers = {"content-type": self.headers.get("content-type", "application/json")}
        if self.auth_style == "azure":
            headers["api-key"] = self.credential
        else:
            headers["authorization"] = f"Bearer {self.credential}"
        request = urllib.request.Request(
            self.upstream_url,
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=180) as response:  # noqa: S310 - configured provider
                self._send(response.status, response.read(), response.headers.get("content-type"))
        except urllib.error.HTTPError as exc:
            self._send(exc.code, exc.read(), exc.headers.get("content-type"))
        except Exception as exc:  # noqa: BLE001
            self._send(502, json.dumps({"error": str(exc)}).encode())

    def _send(self, status: int, body: bytes, content_type: str | None = None) -> None:
        self.send_response(status)
        self.send_header(
            "content-type",
            _safe_content_type(content_type, "application/json"),
        )
        self.send_header("content-length", str(len(body)))
        self.send_header("connection", "close")
        self.end_headers()
        self.wfile.write(body)


def _start_model_proxy(
    spec: "ModelProxySpec", *, access_token: str
) -> tuple[ThreadingHTTPServer, threading.Thread, int]:
    credential = os.environ.get(spec.credential_env, "")
    if not credential:
        raise SandboxRuntimeError(
            f"model_proxy requires host environment variable {spec.credential_env!r}; "
            "its value is kept on the host and is never passed to the container"
        )
    port = _free_port()
    handler = type(
        "SandboxModelProxyHandler",
        (_ModelProxyHandler,),
        {
            "upstream_url": spec.upstream_url,
            "credential": credential,
            "auth_style": spec.auth_style,
            "model": spec.model,
            "access_token": access_token,
        },
    )
    server = ThreadingHTTPServer(("0.0.0.0", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, port


@dataclass(frozen=True)
class ModelProxySpec:
    upstream_url: str
    credential_env: str
    auth_style: str = "bearer"
    model: str | None = None
    container_base_url_env: str = "OPENAI_BASE_URL"
    container_key_env: str = "OPENAI_API_KEY"


@dataclass(frozen=True)
class ContainerSpec:
    image: str
    container_port: int
    command: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    health_path: str = "/health"
    endpoint_path: str = "/chat"
    startup_timeout_s: float = 60.0
    egress_allow_hosts: tuple[str, ...] = ()
    model_proxy: ModelProxySpec | None = None
    memory: str = "1g"
    cpus: float = 1.0
    pids_limit: int = 256
    user: str = "65534:65534"
    case_id: str | None = None


_RUNTIME_OWNED_CONTAINER_ENV = frozenset({
    "ACTION_MEDIATION_POLICY",
    "ACTION_MEDIATION_MOCKS",
    "ACTION_MEDIATION_CASSETTES",
    "ACTION_MEDIATION_LEDGER",
    "ASSERT_SANDBOX_CASE_ID",
    "ASSERT_SANDBOX_OUTPUT",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "http_proxy",
    "https_proxy",
    "NO_PROXY",
    "no_proxy",
})


def _runtime_owned_container_env(spec: ContainerSpec) -> frozenset[str]:
    """Return environment keys whose values define the sandbox trust boundary."""
    keys = set(_RUNTIME_OWNED_CONTAINER_ENV)
    if spec.model_proxy is not None:
        model_keys = {
            spec.model_proxy.container_base_url_env,
            spec.model_proxy.container_key_env,
        }
        collisions = model_keys & keys
        if collisions:
            joined = ", ".join(sorted(collisions))
            raise SandboxRuntimeError(
                "target.model_proxy container environment keys collide with "
                f"ASSERT-owned sandbox controls: {joined}"
            )
        if len(model_keys) != 2:
            raise SandboxRuntimeError(
                "target.model_proxy container_base_url_env and container_key_env "
                "must be different"
            )
        keys.update(model_keys)
    return frozenset(keys)


@dataclass
class SandboxHandle:
    container: str
    network: str
    relay_container: str
    relay_network: str
    endpoint_url: str
    output_dir: Path
    egress_log: Path
    policy_json: Path
    mocks_json: Path
    egress_server: ThreadingHTTPServer
    egress_thread: threading.Thread
    model_server: ThreadingHTTPServer | None = None
    model_thread: threading.Thread | None = None
    egress_offset: int = 0

    def new_egress_rows(self) -> list[dict[str, Any]]:
        rows, self.egress_offset = _read_jsonl(self.egress_log, self.egress_offset)
        return rows

    def stop(self) -> None:
        commands = (
            ("rm", "-f", self.container),
            ("rm", "-f", self.relay_container),
            ("network", "rm", self.network),
            ("network", "rm", self.relay_network),
        )
        errors = _cleanup_docker(commands)
        for server in (self.egress_server, self.model_server):
            if server is None:
                continue
            try:
                server.shutdown()
                server.server_close()
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
        if errors:
            raise SandboxRuntimeError(
                "sandbox cleanup was incomplete: " + "; ".join(str(error) for error in errors)
            ) from errors[0]


def _compile_yaml(path: Path, destination: Path, default: dict[str, Any]) -> None:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else default
    destination.write_text(json.dumps(data or default, indent=2), encoding="utf-8")
    destination.chmod(0o444)


def start_container(
    spec: ContainerSpec,
    *,
    policy_path: Path,
    mocks_path: Path | None,
    cassette_dir: Path | None = None,
    output_dir: Path,
) -> SandboxHandle:
    """Start a disposable, deny-by-default container and wait for readiness."""
    if not docker_available():
        raise SandboxRuntimeError(
            "Docker is required for target.sandbox but the Docker daemon is not available"
        )

    runtime_owned_env = _runtime_owned_container_env(spec)
    overridden = sorted(set(spec.env) & runtime_owned_env)
    if overridden:
        raise SandboxRuntimeError(
            "target.env cannot override ASSERT-owned sandbox controls: "
            + ", ".join(overridden)
        )

    forbidden_fragments = ("key", "token", "secret", "password", "credential")
    for key in spec.env:
        if any(fragment in key.lower() for fragment in forbidden_fragments):
            raise SandboxRuntimeError(
                f"target.env.{key} looks credential-bearing. Keep real credentials on the host "
                "with target.model_proxy; do not inject them into the sandbox."
            )
    if spec.model_proxy is not None and not os.environ.get(spec.model_proxy.credential_env, ""):
        raise SandboxRuntimeError(
            f"model_proxy requires host environment variable {spec.model_proxy.credential_env!r}; "
            "its value is kept on the host and is never passed to the container"
        )

    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    # The stock default runs as an unprivileged UID that need not exist on the
    # host. This directory is dedicated per case and contains only sandbox
    # output, so make the mount writable without weakening any source/policy
    # mount. It is removed with the owning session.
    output_dir.chmod(0o777)
    # The evaluated target may control every byte under its writable output
    # mount. Keep proxy-generated evidence in a sibling host-only directory so
    # the target cannot rewrite or delete the ledger ASSERT later consumes.
    audit_dir = output_dir.parent / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    audit_dir.chmod(0o700)
    egress_log = audit_dir / "egress.jsonl"
    config_dir = output_dir.parent / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_dir.chmod(0o755)
    policy_json = config_dir / "policy.json"
    mocks_json = config_dir / "mocks.json"
    _compile_yaml(policy_path, policy_json, {"interactions": [], "default": {"mode": "block"}})
    if mocks_path is not None:
        _compile_yaml(mocks_path, mocks_json, {"version": 1, "mocks": []})
    else:
        mocks_json.write_text('{"version":1,"mocks":[]}', encoding="utf-8")
        mocks_json.chmod(0o444)

    token = secrets.token_hex(6)
    container_name = f"assert-sandbox-{token}"
    network_name = f"assert-sandbox-net-{token}"
    relay_container = f"assert-sandbox-relay-{token}"
    relay_network = f"assert-sandbox-relay-net-{token}"
    egress_token = secrets.token_hex(16)
    egress_server, egress_thread, egress_port = _start_egress_proxy(
        audit_log=egress_log,
        allow_hosts=spec.egress_allow_hosts,
        proxy_token=egress_token,
    )
    model_server: ThreadingHTTPServer | None = None
    model_thread: threading.Thread | None = None

    try:
        _docker(
            "network",
            "create",
            "--internal",
            "-o",
            "com.docker.network.bridge.inhibit_ipv4=true",
            "-o",
            "com.docker.network.bridge.enable_ip_masquerade=false",
            network_name,
        )
        _docker("network", "create", relay_network)

        relay_specs = [
            {
                "listen_port": _RELAY_TARGET_PORT,
                "upstream": "target",
                "upstream_port": spec.container_port,
            },
            {
                "listen_port": _RELAY_EGRESS_PORT,
                "upstream": "host",
                "upstream_port": egress_port,
            },
        ]

        synthetic_key: str | None = None
        if spec.model_proxy is not None:
            synthetic_key = f"assert-sandbox-{secrets.token_hex(12)}"
            model_server, model_thread, model_port = _start_model_proxy(
                spec.model_proxy,
                access_token=synthetic_key,
            )
            relay_specs.append({
                "listen_port": _RELAY_MODEL_PORT,
                "upstream": "host",
                "upstream_port": model_port,
            })

        relay_script = Path(__file__).with_name("tcp_relay.py").resolve()
        _docker(
            "run",
            "-d",
            "--name",
            relay_container,
            "--network",
            relay_network,
            "--add-host",
            "host.docker.internal:host-gateway",
            "--read-only",
            "--user",
            "65534:65534",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            "64",
            "--memory",
            "128m",
            "--cpus",
            "0.25",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=16m",
            "-p",
            f"127.0.0.1::{_RELAY_TARGET_PORT}",
            "-v",
            f"{relay_script}:/relay.py:ro",
            "-e",
            f"ASSERT_SANDBOX_RELAY_SPECS={json.dumps(relay_specs, separators=(',', ':'))}",
            _RELAY_IMAGE,
            "python",
            "/relay.py",
        )
        _docker(
            "network",
            "connect",
            "--alias",
            _RELAY_ALIAS,
            network_name,
            relay_container,
        )

        args = [
            "run",
            "-d",
            "--name",
            container_name,
            "--network",
            network_name,
            "--network-alias",
            _TARGET_ALIAS,
            "--read-only",
            "--user",
            spec.user,
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            str(spec.pids_limit),
            "--memory",
            spec.memory,
            "--cpus",
            str(spec.cpus),
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m",
            "--tmpfs",
            "/run:rw,noexec,nosuid,size=16m",
            "-v",
            f"{policy_json}:/sandbox/policy.json:ro",
            "-v",
            f"{mocks_json}:/sandbox/mocks.json:ro",
            "-v",
            f"{output_dir}:/sandbox/output:rw",
            "-e",
            "ACTION_MEDIATION_POLICY=/sandbox/policy.json",
            "-e",
            "ACTION_MEDIATION_MOCKS=/sandbox/mocks.json",
            "-e",
            "ASSERT_SANDBOX_OUTPUT=/sandbox/output",
            "-e",
            "ACTION_MEDIATION_LEDGER=/sandbox/output/mediation.jsonl",
            "-e",
            f"HTTP_PROXY=http://assert:{egress_token}@{_RELAY_ALIAS}:{_RELAY_EGRESS_PORT}",
            "-e",
            f"HTTPS_PROXY=http://assert:{egress_token}@{_RELAY_ALIAS}:{_RELAY_EGRESS_PORT}",
            "-e",
            f"http_proxy=http://assert:{egress_token}@{_RELAY_ALIAS}:{_RELAY_EGRESS_PORT}",
            "-e",
            f"https_proxy=http://assert:{egress_token}@{_RELAY_ALIAS}:{_RELAY_EGRESS_PORT}",
        ]
        if spec.case_id:
            args += ["-e", f"ASSERT_SANDBOX_CASE_ID={spec.case_id}"]
        if cassette_dir is not None:
            args += [
                "-v", f"{cassette_dir.resolve()}:/sandbox/cassettes:ro",
                "-e", "ACTION_MEDIATION_CASSETTES=/sandbox/cassettes",
            ]

        no_proxy = ["localhost", "127.0.0.1"]
        if spec.model_proxy is not None:
            base_url = f"http://{_RELAY_ALIAS}:{_RELAY_MODEL_PORT}/v1"
            args += [
                "-e",
                f"{spec.model_proxy.container_base_url_env}={base_url}",
                "-e",
                f"{spec.model_proxy.container_key_env}={synthetic_key}",
            ]
            no_proxy.append(_RELAY_ALIAS)

        joined_no_proxy = ",".join(no_proxy)
        args += ["-e", f"NO_PROXY={joined_no_proxy}", "-e", f"no_proxy={joined_no_proxy}"]

        for key, value in spec.env.items():
            args += ["-e", f"{key}={value}"]

        args.append(spec.image)
        args.extend(spec.command)
        _docker(*args)

        port_result = _docker("port", relay_container, f"{_RELAY_TARGET_PORT}/tcp")
        address = port_result.stdout.strip().splitlines()[0]
        host_port = int(address.rsplit(":", 1)[1])
        endpoint_url = f"http://127.0.0.1:{host_port}{spec.endpoint_path}"
        _wait_http(
            f"http://127.0.0.1:{host_port}{spec.health_path}",
            spec.startup_timeout_s,
        )
        return SandboxHandle(
            container=container_name,
            network=network_name,
            relay_container=relay_container,
            relay_network=relay_network,
            endpoint_url=endpoint_url,
            output_dir=output_dir,
            egress_log=egress_log,
            policy_json=policy_json,
            mocks_json=mocks_json,
            egress_server=egress_server,
            egress_thread=egress_thread,
            model_server=model_server,
            model_thread=model_thread,
        )
    except Exception:
        # Preserve the startup error while releasing every resource that was
        # created. Cleanup failures are secondary here and must not stop later
        # cleanup steps from running.
        commands = (
            ("rm", "-f", container_name),
            ("rm", "-f", relay_container),
            ("network", "rm", network_name),
            ("network", "rm", relay_network),
        )
        cleanup_errors = _cleanup_docker(commands)
        for cleanup_error in cleanup_errors:
            log.warning("sandbox Docker cleanup also failed after startup error: %s", cleanup_error)
        for server in (egress_server, model_server):
            if server is not None:
                try:
                    server.shutdown()
                    server.server_close()
                except Exception:  # noqa: BLE001
                    pass
        raise


def egress_event(row: dict[str, Any], *, case_id: str | None = None) -> dict[str, Any]:
    evidence = {
        "channel": "egress",
        "ts": row.get("ts"),
        "host": str(row.get("host") or ""),
        "port": int(row.get("port") or 0),
        "method": str(row.get("method") or ""),
        "path": str(row.get("path") or ""),
        "decision": str(row.get("decision") or ""),
    }
    if case_id:
        evidence["case_id"] = case_id
    return {
        "role": "tool_result",
        "tool_name": "network_egress",
        "tool_args": {
            "host": evidence["host"],
            "port": evidence["port"],
            "method": evidence["method"],
            "path": evidence["path"],
        },
        "tool_call_id": f"egress-{secrets.token_hex(8)}",
        "content": json.dumps(evidence, ensure_ascii=False, sort_keys=True),
    }
