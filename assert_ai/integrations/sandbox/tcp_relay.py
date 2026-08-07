# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Tiny trusted TCP relay used by the stock sandbox runtime.

The untrusted target is attached only to a Docker network with no host gateway.
This process is the narrow bridge for the three connections the runtime already
owns: target ingress, audited HTTP(S) egress, and optional model proxy traffic.
It intentionally contains no policy or credential logic; authenticated host
proxies remain the enforcement points.
"""
from __future__ import annotations

import json
import os
import select
import socket
import socketserver
import threading
from dataclasses import dataclass


@dataclass(frozen=True)
class RelaySpec:
    listen_port: int
    upstream_host: str
    upstream_port: int


def _load_specs() -> list[RelaySpec]:
    try:
        raw = json.loads(os.environ["ASSERT_SANDBOX_RELAY_SPECS"])
    except (KeyError, json.JSONDecodeError) as exc:
        raise SystemExit("ASSERT_SANDBOX_RELAY_SPECS must be valid JSON") from exc
    if not isinstance(raw, list) or not raw:
        raise SystemExit("ASSERT_SANDBOX_RELAY_SPECS must be a non-empty list")

    specs: list[RelaySpec] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise SystemExit("each relay spec must be an object")
        try:
            upstream = str(entry["upstream"])
            if upstream == "target":
                upstream_host = "assert-sandbox-target"
            elif upstream == "host":
                upstream_host = "host.docker.internal"
            else:
                raise SystemExit("relay upstream is outside the stock sandbox topology")
            spec = RelaySpec(
                listen_port=int(entry["listen_port"]),
                upstream_host=upstream_host,
                upstream_port=int(entry["upstream_port"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise SystemExit("invalid relay spec") from exc
        if not 1024 <= spec.listen_port <= 65535:
            raise SystemExit("relay listen ports must be between 1024 and 65535")
        if not 1 <= spec.upstream_port <= 65535:
            raise SystemExit("relay upstream ports must be between 1 and 65535")
        specs.append(spec)
    if len({spec.listen_port for spec in specs}) != len(specs):
        raise SystemExit("relay listen ports must be unique")
    return specs


class _ThreadingRelayServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class _RelayHandler(socketserver.BaseRequestHandler):
    upstream_host = ""
    upstream_port = 0

    def handle(self) -> None:
        try:
            upstream = socket.create_connection(
                (self.upstream_host, self.upstream_port), timeout=30
            )
        except OSError:
            return
        sockets = [self.request, upstream]
        try:
            while True:
                readable, _, exceptional = select.select(sockets, [], sockets, 300)
                if exceptional or not readable:
                    return
                for source in readable:
                    destination = upstream if source is self.request else self.request
                    data = source.recv(65536)
                    if not data:
                        return
                    destination.sendall(data)
        except OSError:
            return
        finally:
            upstream.close()


def main() -> None:
    servers: list[_ThreadingRelayServer] = []
    for index, spec in enumerate(_load_specs()):
        handler = type(
            f"SandboxRelayHandler{index}",
            (_RelayHandler,),
            {
                "upstream_host": spec.upstream_host,
                "upstream_port": spec.upstream_port,
            },
        )
        server = _ThreadingRelayServer(("0.0.0.0", spec.listen_port), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)

    try:
        threading.Event().wait()
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    main()
