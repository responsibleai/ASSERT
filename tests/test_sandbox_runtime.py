# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import asyncio
import base64
import http.client
import importlib.resources
import json
from pathlib import Path
import threading
from types import SimpleNamespace
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from assert_ai.config import parse_pipeline_config, parse_target_config
from assert_ai.core.config_model import InferenceConfig, TargetConfig
from assert_ai.core.model_client import Message
from assert_ai.integrations.sandbox import load_setup
from assert_ai.integrations.sandbox import runtime as sandbox_runtime
from assert_ai.integrations.sandbox.runtime import (
    ContainerSpec,
    ModelProxySpec,
    SandboxRuntimeError,
)
from assert_ai.integrations.sandbox.session import SandboxedEndpointSession
from assert_ai.integrations.sandbox import session as sandbox_session
from assert_ai.stages.inference import _build_target_session
from assert_ai.stages.inference import _inference_config_fingerprint


def _files(tmp_path: Path) -> tuple[Path, Path, Path]:
    policy = tmp_path / "policy.yaml"
    mocks = tmp_path / "mocks.yaml"
    setup = tmp_path / "setup.yaml"
    policy.write_text("interactions: []\ndefault: {mode: block}\n", encoding="utf-8")
    mocks.write_text("version: 1\nmocks: []\n", encoding="utf-8")
    return policy, mocks, setup


def test_sandbox_target_is_a_first_class_exclusive_target():
    target = TargetConfig(sandbox="sandbox.yaml")
    assert target.is_sandbox
    with pytest.raises(ValueError, match="exactly one"):
        TargetConfig(sandbox="sandbox.yaml", endpoint="http://localhost/chat")


def test_stock_docker_assets_are_packaged_with_copyable_agent():
    assets = importlib.resources.files("assert_ai.integrations.sandbox.stock")
    root = Path(__file__).resolve().parents[1]
    dockerfile = assets.joinpath("Dockerfile").read_text()
    assert "ARG ASSERT_AI_PACKAGE=assert-ai" in dockerfile
    assert "USER 65534:65534" in dockerfile
    assert assets.joinpath("server.py").read_text() == (
        root / "examples/sandbox_action_mediation/stock_agent/server.py"
    ).read_text()


def test_parser_accepts_sandbox_target():
    target = parse_target_config(
        {"sandbox": "sandbox.yaml"}, field_name="pipeline.inference.target"
    )
    assert target.sandbox == "sandbox.yaml"


def test_default_model_is_not_injected_into_sandbox_target():
    pipeline = parse_pipeline_config({
        "default_model": {"name": "openai/test"},
        "pipeline": {"inference": {"target": {"sandbox": "sandbox.yaml"}}},
    })
    assert pipeline is not None and pipeline.target is not None
    assert pipeline.target.sandbox == "sandbox.yaml"
    assert pipeline.target.model is None


def test_container_setup_parses_stock_sandbox_options(tmp_path):
    _, _, setup = _files(tmp_path)
    setup.write_text(
        """version: 1
target:
  kind: container
  image: example/agent:latest
  port: 8080
  command: [python, /app/server.py]
  health_path: /ready
  endpoint_path: /chat
  egress:
    allow_hosts: [example.com]
  memory: 512m
  cpus: 0.5
  pids_limit: 64
policy: ./policy.yaml
mocks: ./mocks.yaml
""",
        encoding="utf-8",
    )
    loaded = load_setup(setup)
    assert loaded.target.kind == "container"
    assert loaded.target.port == 8080
    assert loaded.target.health_path == "/ready"
    assert loaded.target.egress_allow_hosts == ("example.com",)
    assert loaded.target.memory == "512m"
    assert loaded.policy_path == tmp_path / "policy.yaml"
    assert loaded.mocks_path == tmp_path / "mocks.yaml"


def test_container_setup_requires_a_port(tmp_path):
    _, _, setup = _files(tmp_path)
    setup.write_text(
        "version: 1\ntarget: {kind: container, image: x}\npolicy: ./policy.yaml\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="requires `port:`"):
        load_setup(setup)


def test_inference_builds_owned_sandbox_session_relative_to_config(tmp_path):
    _, _, setup = _files(tmp_path)
    setup.write_text(
        "version: 1\ntarget: {kind: endpoint, url: 'http://localhost/chat'}\n"
        "policy: ./policy.yaml\nmocks: ./mocks.yaml\n",
        encoding="utf-8",
    )
    config = tmp_path / "eval.yaml"
    config.write_text("suite: x\n", encoding="utf-8")
    session = _build_target_session(
        target=TargetConfig(sandbox="setup.yaml"),
        test_case_payload={},
        inference=InferenceConfig(),
        max_tokens=100,
        config_path=config,
    )
    assert isinstance(session, SandboxedEndpointSession)
    assert session.setup.source_path == setup


def test_policy_or_mock_change_invalidates_inference_cache(tmp_path):
    policy, _, setup = _files(tmp_path)
    setup.write_text(
        "version: 1\ntarget: {kind: endpoint, url: 'http://localhost/chat'}\n"
        "policy: ./policy.yaml\nmocks: ./mocks.yaml\n",
        encoding="utf-8",
    )
    config = tmp_path / "eval.yaml"
    target = TargetConfig(sandbox="setup.yaml")
    before = _inference_config_fingerprint(
        target, None, 100, config_path=config
    )
    policy.write_text(
        "interactions: [{match: send_message, mode: block}]\ndefault: {mode: block}\n",
        encoding="utf-8",
    )
    after = _inference_config_fingerprint(
        target, None, 100, config_path=config
    )
    assert after != before


def test_secret_like_container_env_is_rejected_before_docker(tmp_path, monkeypatch):
    policy, mocks, _ = _files(tmp_path)
    monkeypatch.setattr(sandbox_runtime, "docker_available", lambda: True)
    spec = ContainerSpec(
        image="example",
        container_port=8080,
        env={"API_KEY": "must-not-enter"},
    )
    with pytest.raises(SandboxRuntimeError, match="credential-bearing"):
        sandbox_runtime.start_container(
            spec,
            policy_path=policy,
            mocks_path=mocks,
            output_dir=tmp_path / "out",
        )


def test_model_proxy_requires_host_credential_without_passing_it_to_container(tmp_path, monkeypatch):
    policy, mocks, _ = _files(tmp_path)
    monkeypatch.setattr(sandbox_runtime, "docker_available", lambda: True)
    monkeypatch.delenv("PRIVATE_PROVIDER_KEY", raising=False)
    spec = ContainerSpec(
        image="example",
        container_port=8080,
        model_proxy=ModelProxySpec(
            upstream_url="https://provider.invalid/chat",
            credential_env="PRIVATE_PROVIDER_KEY",
        ),
    )
    with pytest.raises(SandboxRuntimeError, match="PRIVATE_PROVIDER_KEY"):
        sandbox_runtime.start_container(
            spec,
            policy_path=policy,
            mocks_path=mocks,
            output_dir=tmp_path / "out",
        )


def test_docker_command_enforces_stock_containment_and_omits_real_credential(tmp_path, monkeypatch):
    policy, mocks, _ = _files(tmp_path)
    monkeypatch.setenv("PRIVATE_PROVIDER_KEY", "super-secret-real-value")
    monkeypatch.setattr(sandbox_runtime, "docker_available", lambda: True)
    calls: list[tuple[str, ...]] = []

    class Server:
        def shutdown(self): pass
        def server_close(self): pass

    monkeypatch.setattr(
        sandbox_runtime,
        "_start_egress_proxy",
        lambda **kwargs: (Server(), SimpleNamespace(), 9100),
    )
    monkeypatch.setattr(
        sandbox_runtime,
        "_start_model_proxy",
        lambda spec, **kwargs: (Server(), SimpleNamespace(), 9200),
    )
    monkeypatch.setattr(sandbox_runtime, "_wait_http", lambda *args, **kwargs: None)

    def fake_docker(*args: str, check: bool = True):
        calls.append(args)
        if args[:2] == ("port", "assert-sandbox-deadbeef"):
            return SimpleNamespace(stdout="127.0.0.1:49152\n")
        if args and args[0] == "port":
            return SimpleNamespace(stdout="127.0.0.1:49152\n")
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(sandbox_runtime.secrets, "token_hex", lambda n: "deadbeef")
    monkeypatch.setattr(sandbox_runtime, "_docker", fake_docker)
    cassettes = tmp_path / "cassettes"
    cassettes.mkdir()
    handle = sandbox_runtime.start_container(
        ContainerSpec(
            image="example",
            container_port=8080,
            model_proxy=ModelProxySpec(
                upstream_url="https://provider.invalid/chat",
                credential_env="PRIVATE_PROVIDER_KEY",
            ),
        ),
        policy_path=policy,
        mocks_path=mocks,
        cassette_dir=cassettes,
        output_dir=tmp_path / "out",
    )
    run = next(call for call in calls if call and call[0] == "run")
    command = " ".join(run)
    assert "--read-only" in run
    assert "--user" in run and "65534:65534" in run
    assert "--cap-drop" in run and "ALL" in run
    assert "no-new-privileges" in run
    assert "ACTION_MEDIATION_LEDGER=/sandbox/output/mediation.jsonl" in run
    assert "ACTION_MEDIATION_CASSETTES=/sandbox/cassettes" in run
    assert f"{cassettes.resolve()}:/sandbox/cassettes:ro" in run
    assert "enable_ip_masquerade=false" in " ".join(" ".join(c) for c in calls)
    assert "PRIVATE_PROVIDER_KEY" not in command
    assert "super-secret-real-value" not in command
    assert "assert-sandbox-deadbeef" in command
    assert handle.endpoint_url == "http://127.0.0.1:49152/chat"


def test_model_proxy_requires_synthetic_token_and_injects_real_key_host_side(monkeypatch):
    seen: dict[str, str] = {}

    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, format, *args): pass
        def do_POST(self):
            seen["authorization"] = self.headers.get("authorization", "")
            body = b'{"ok":true}'
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("PRIVATE_PROVIDER_KEY", "real-host-only-key")
    spec = ModelProxySpec(
        upstream_url=f"http://127.0.0.1:{upstream.server_port}/chat",
        credential_env="PRIVATE_PROVIDER_KEY",
    )
    proxy, _, port = sandbox_runtime._start_model_proxy(
        spec, access_token="synthetic-container-token"
    )
    try:
        unauthorized = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/chat/completions",
            data=b"{}",
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(unauthorized, timeout=5)
        assert exc.value.code == 401

        authorized = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/chat/completions",
            data=b"{}",
            headers={"authorization": "Bearer synthetic-container-token"},
            method="POST",
        )
        with urllib.request.urlopen(authorized, timeout=5) as response:
            assert response.status == 200
        assert seen["authorization"] == "Bearer real-host-only-key"
    finally:
        proxy.shutdown()
        proxy.server_close()
        upstream.shutdown()
        upstream.server_close()


def test_egress_proxy_requires_synthetic_auth_and_does_not_forward_it(tmp_path, monkeypatch):
    seen: dict[str, str | None] = {}
    monkeypatch.setattr(sandbox_runtime, "validate_endpoint_url", lambda url: None)
    monkeypatch.setattr(sandbox_runtime, "_resolve_public_ip", lambda host: "127.0.0.1")

    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, format, *args): pass
        def do_GET(self):
            seen["proxy_authorization"] = self.headers.get("proxy-authorization")
            body = b"ok"
            self.send_response(200)
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    proxy, _, port = sandbox_runtime._start_egress_proxy(
        audit_log=tmp_path / "egress.jsonl",
        allow_hosts=("127.0.0.1",),
        proxy_token="synthetic-egress-token",
    )
    try:
        unauthenticated = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        unauthenticated.request("GET", f"http://127.0.0.1:{upstream.server_port}/x")
        assert unauthenticated.getresponse().status == 407

        token = base64.b64encode(b"assert:synthetic-egress-token").decode()
        authenticated = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        authenticated.request(
            "GET",
            f"http://127.0.0.1:{upstream.server_port}/x",
            headers={"Proxy-Authorization": f"Basic {token}"},
        )
        assert authenticated.getresponse().status == 200
        assert seen["proxy_authorization"] is None
    finally:
        proxy.shutdown()
        proxy.server_close()
        upstream.shutdown()
        upstream.server_close()


def test_egress_proxy_rejects_private_destination_even_when_allowlisted(tmp_path):
    proxy, _, port = sandbox_runtime._start_egress_proxy(
        audit_log=tmp_path / "egress.jsonl",
        allow_hosts=("127.0.0.1",),
        proxy_token="synthetic-egress-token",
    )
    try:
        token = base64.b64encode(b"assert:synthetic-egress-token").decode()
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        connection.request(
            "GET",
            "http://127.0.0.1:12345/private",
            headers={"Proxy-Authorization": f"Basic {token}"},
        )
        assert connection.getresponse().status == 403
        rows = [
            json.loads(line)
            for line in (tmp_path / "egress.jsonl").read_text().splitlines()
        ]
        assert rows[-1]["decision"] == "denied"
    finally:
        proxy.shutdown()
        proxy.server_close()


def test_session_close_always_removes_owned_container(tmp_path):
    _, _, setup = _files(tmp_path)
    setup.write_text(
        "version: 1\ntarget: {kind: endpoint, url: 'http://localhost/chat'}\n"
        "policy: ./policy.yaml\nmocks: ./mocks.yaml\n",
        encoding="utf-8",
    )
    session = SandboxedEndpointSession(setup_path=setup)
    stopped = {"value": False}

    class FakeHandle:
        def stop(self): stopped["value"] = True

    class FakeEndpoint:
        async def close(self): pass

    session._handle = FakeHandle()  # type: ignore[assignment]
    session._endpoint = FakeEndpoint()  # type: ignore[assignment]
    asyncio.run(session.close())
    assert stopped["value"] is True


def test_handle_cleanup_releases_servers_even_if_docker_disappears(tmp_path, monkeypatch):
    closed: list[str] = []

    class Server:
        def __init__(self, name): self.name = name
        def shutdown(self): closed.append(f"shutdown:{self.name}")
        def server_close(self): closed.append(f"close:{self.name}")

    def missing_docker(*args, **kwargs):
        raise SandboxRuntimeError("docker disappeared")

    monkeypatch.setattr(sandbox_runtime, "_docker", missing_docker)
    handle = sandbox_runtime.SandboxHandle(
        container="c",
        network="n",
        endpoint_url="http://localhost/chat",
        output_dir=tmp_path,
        egress_log=tmp_path / "e",
        policy_json=tmp_path / "p",
        mocks_json=tmp_path / "m",
        egress_server=Server("egress"),  # type: ignore[arg-type]
        egress_thread=SimpleNamespace(),  # type: ignore[arg-type]
        model_server=Server("model"),  # type: ignore[arg-type]
    )
    with pytest.raises(SandboxRuntimeError, match="cleanup was incomplete"):
        handle.stop()
    assert closed == [
        "shutdown:egress", "close:egress", "shutdown:model", "close:model"
    ]


def test_open_failure_after_docker_start_cleans_up_container_and_workdir(tmp_path, monkeypatch):
    _, _, setup = _files(tmp_path)
    setup.write_text(
        "version: 1\ntarget: {kind: container, image: x, port: 8080}\n"
        "policy: ./policy.yaml\nmocks: ./mocks.yaml\n",
        encoding="utf-8",
    )
    stopped = {"value": False}

    class FakeHandle:
        endpoint_url = "http://127.0.0.1:12345/chat"
        def stop(self): stopped["value"] = True

    class BrokenEndpoint:
        def __init__(self, **kwargs): raise RuntimeError("endpoint init failed")

    monkeypatch.setattr(sandbox_session, "start_container", lambda *args, **kwargs: FakeHandle())
    monkeypatch.setattr(sandbox_session, "HTTPEndpointSession", BrokenEndpoint)
    session = SandboxedEndpointSession(setup_path=setup)
    with pytest.raises(RuntimeError, match="endpoint init failed"):
        asyncio.run(session.open())
    assert stopped["value"] is True
    assert session._handle is None
    assert session._workdir is None


def test_egress_rows_become_assert_tool_evidence(tmp_path):
    _, _, setup = _files(tmp_path)
    setup.write_text(
        "version: 1\ntarget: {kind: endpoint, url: 'http://localhost/chat'}\n"
        "policy: ./policy.yaml\nmocks: ./mocks.yaml\n",
        encoding="utf-8",
    )
    session = SandboxedEndpointSession(setup_path=setup)

    class FakeEndpoint:
        async def run_turn(self, messages):
            from assert_ai.core.session import TurnResult
            return TurnResult(
                text="done",
                state_messages=[],
                interaction_messages=[{"role": "assistant", "content": "done"}],
            )

    class FakeHandle:
        def new_egress_rows(self):
            return [{
                "ts": "now", "host": "bad.example", "port": 443,
                "method": "CONNECT", "path": "", "decision": "denied",
            }]

    session._endpoint = FakeEndpoint()  # type: ignore[assignment]
    session._handle = FakeHandle()  # type: ignore[assignment]
    result = asyncio.run(session.run_turn([Message(role="user", content="go")]))
    assert any(
        message.get("role") == "tool" and message.get("function") == "network_egress"
        for message in result.interaction_messages
    )
    assert "bad.example" in json.dumps(result.interaction_messages)
