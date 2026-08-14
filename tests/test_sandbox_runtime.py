# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import asyncio
import base64
import http.client
import http.server
import importlib.resources
import json
import runpy
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from assert_ai.config import parse_pipeline_config, parse_target_config
from assert_ai.core.config_model import InferenceConfig, TargetConfig
from assert_ai.core.model_client import Message
from assert_ai.integrations.sandbox import load_setup, tcp_relay
from assert_ai.integrations.sandbox import runtime as sandbox_runtime
from assert_ai.integrations.sandbox import session as sandbox_session
from assert_ai.integrations.sandbox.mocks import MockBackendError, MockCall
from assert_ai.integrations.sandbox.runtime import (
    ContainerSpec,
    ModelProxySpec,
    SandboxRuntimeError,
)
from assert_ai.integrations.sandbox.session import SandboxedEndpointSession
from assert_ai.stages import inference as inference_stage
from assert_ai.stages.inference import (
    _build_target_session,
    _inference_config_fingerprint,
)


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


@pytest.mark.parametrize(
    ("value", "default", "expected"),
    [
        ("application/json; charset=utf-8", "application/octet-stream", "application/json; charset=utf-8"),
        ("text/plain\r\nx-injected: yes", "application/octet-stream", "application/octet-stream"),
        ("text/plain\nx-injected: yes", "application/json", "application/json"),
        ("text/plain\x00", "application/json", "application/json"),
        ("application/💣", "application/json", "application/json"),
    ],
)
def test_untrusted_proxy_content_type_cannot_split_response_headers(value, default, expected):
    """An upstream response cannot append headers through Content-Type."""
    assert sandbox_runtime._safe_content_type(value, default) == expected


def test_model_proxy_send_uses_fixed_type_for_malicious_upstream_header(monkeypatch):
    """The model-proxy response path applies the sanitizer before send_header."""
    handler = object.__new__(sandbox_runtime._ModelProxyHandler)
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(handler, "send_response", lambda status: None)
    monkeypatch.setattr(handler, "send_header", lambda name, value: sent.append((name, value)))
    monkeypatch.setattr(handler, "end_headers", lambda: None)
    monkeypatch.setattr(
        handler,
        "wfile",
        SimpleNamespace(write=lambda body: None),
        raising=False,
    )

    handler._send(200, b"{}", "application/json\r\nx-injected: yes")

    assert ("content-type", "application/json") in sent
    assert not any("x-injected" in value for _, value in sent)


def test_stock_docker_assets_are_packaged_with_copyable_agent():
    assets = importlib.resources.files("assert_ai.integrations.sandbox.stock")
    root = Path(__file__).resolve().parents[1]
    dockerfile = assets.joinpath("Dockerfile").read_text()
    assert "ARG ASSERT_AI_PACKAGE=assert-ai" in dockerfile
    assert "USER 65534:65534" in dockerfile
    assert assets.joinpath("server.py").read_text() == (
        root / "examples/sandbox_action_mediation/stock_agent/server.py"
    ).read_text()


def test_stock_server_passes_top_level_cassette_dir_to_mock_library(tmp_path, monkeypatch):
    """Container replay rules use the same top-level cassette mount as host mode."""
    policy = tmp_path / "policy.json"
    mocks = tmp_path / "mocks.json"
    cassettes = tmp_path / "cassettes"
    cassettes.mkdir()
    policy.write_text(
        json.dumps({"interactions": [], "default": {"mode": "block"}}),
        encoding="utf-8",
    )
    mocks.write_text(
        json.dumps({
            "version": 1,
            "mocks": [{
                "tool": "lookup",
                "backend": "replay",
                "cassette_file": "lookup",
            }],
        }),
        encoding="utf-8",
    )
    (cassettes / "lookup.json").write_text('{"source": "cassette"}', encoding="utf-8")
    monkeypatch.setenv("ACTION_MEDIATION_POLICY", str(policy))
    monkeypatch.setenv("ACTION_MEDIATION_MOCKS", str(mocks))
    monkeypatch.setenv("ACTION_MEDIATION_CASSETTES", str(cassettes))

    class FakeHTTPServer:
        def __init__(self, *args, **kwargs):
            pass

        def serve_forever(self):
            return None

    monkeypatch.setattr(http.server, "ThreadingHTTPServer", FakeHTTPServer)
    server_path = Path(__file__).resolve().parents[1] / (
        "assert_ai/integrations/sandbox/stock/server.py"
    )
    namespace = runpy.run_path(str(server_path), run_name="assert_stock_server_test")

    library = namespace["MOCKS"]
    assert library.cassette_dir == cassettes
    resolution = library.resolve(MockCall("lookup", {}))
    assert resolution is not None
    assert resolution.value == {"source": "cassette"}


def test_relay_specs_are_limited_to_the_owned_sandbox_topology(monkeypatch):
    monkeypatch.setenv(
        "ASSERT_SANDBOX_RELAY_SPECS",
        json.dumps([
            {
                "listen_port": 18080,
                "upstream": "target",
                "upstream_port": 8080,
            },
            {
                "listen_port": 18081,
                "upstream": "host",
                "upstream_port": 9000,
            },
        ]),
    )
    specs = tcp_relay._load_specs()
    assert [spec.listen_port for spec in specs] == [18080, 18081]

    monkeypatch.setenv(
        "ASSERT_SANDBOX_RELAY_SPECS",
        json.dumps([{
            "listen_port": 18080,
            "upstream": "unrelated-service",
            "upstream_port": 8080,
        }]),
    )
    with pytest.raises(SystemExit, match="outside the stock sandbox topology"):
        tcp_relay._load_specs()


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


def test_cassette_change_invalidates_inference_cache(tmp_path):
    _, mocks, setup = _files(tmp_path)
    cassettes = tmp_path / "cassettes"
    cassettes.mkdir()
    cassette = cassettes / "lookup.json"
    cassette.write_text('{"value":"before"}', encoding="utf-8")
    mocks.write_text(
        "version: 1\nmocks:\n  - tool: lookup\n    backend: replay\n    cassette_file: lookup\n",
        encoding="utf-8",
    )
    setup.write_text(
        "version: 1\ntarget: {kind: endpoint, url: 'http://localhost/chat'}\n"
        "policy: ./policy.yaml\nmocks: ./mocks.yaml\ncassettes: ./cassettes\n",
        encoding="utf-8",
    )
    config = tmp_path / "eval.yaml"
    target = TargetConfig(sandbox="setup.yaml")

    before = _inference_config_fingerprint(target, None, 100, config_path=config)
    cassette.write_text('{"value":"after"}', encoding="utf-8")
    after = _inference_config_fingerprint(target, None, 100, config_path=config)

    assert after != before


def test_cassette_fingerprint_does_not_follow_unrelated_symlink(
    tmp_path, symlink_or_skip
):
    _, mocks, setup = _files(tmp_path)
    cassettes = tmp_path / "cassettes"
    cassettes.mkdir()
    (cassettes / "lookup.json").write_text('{"value":"safe"}', encoding="utf-8")
    outside = tmp_path / "outside.json"
    outside.write_text('{"secret":"before"}', encoding="utf-8")
    symlink_or_skip(cassettes / "unrelated.json", outside)
    mocks.write_text(
        "version: 1\nmocks:\n  - tool: lookup\n    backend: replay\n"
        "    cassette_file: lookup\n",
        encoding="utf-8",
    )
    setup.write_text(
        "version: 1\ntarget: {kind: endpoint, url: 'http://localhost/chat'}\n"
        "policy: ./policy.yaml\nmocks: ./mocks.yaml\ncassettes: ./cassettes\n",
        encoding="utf-8",
    )
    config = tmp_path / "eval.yaml"
    target = TargetConfig(sandbox="setup.yaml")

    before = _inference_config_fingerprint(target, None, 100, config_path=config)
    outside.write_text('{"secret":"after"}', encoding="utf-8")
    after = _inference_config_fingerprint(target, None, 100, config_path=config)

    assert after == before


def test_setup_rejects_missing_replay_cassette_before_run(tmp_path):
    _, mocks, setup = _files(tmp_path)
    cassettes = tmp_path / "cassettes"
    cassettes.mkdir()
    mocks.write_text(
        "version: 1\nmocks:\n  - tool: lookup\n    backend: replay\n    cassette_file: missing\n",
        encoding="utf-8",
    )
    setup.write_text(
        "version: 1\ntarget: {kind: endpoint, url: 'http://localhost/chat'}\n"
        "policy: ./policy.yaml\nmocks: ./mocks.yaml\ncassettes: ./cassettes\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="replay cassette file not found.*lookup"):
        load_setup(setup)


def test_setup_accepts_inline_replay_without_cassette_directory(tmp_path):
    _, mocks, setup = _files(tmp_path)
    mocks.write_text(
        "version: 1\nmocks:\n  - tool: lookup\n    backend: replay\n"
        "    cassette: {value: inline}\n",
        encoding="utf-8",
    )
    setup.write_text(
        "version: 1\ntarget: {kind: endpoint, url: 'http://localhost/chat'}\n"
        "policy: ./policy.yaml\nmocks: ./mocks.yaml\n",
        encoding="utf-8",
    )

    loaded = load_setup(setup)

    assert loaded.cassette_dir is None


def test_setup_rejects_replay_cassette_path_escape(tmp_path):
    _, mocks, setup = _files(tmp_path)
    cassettes = tmp_path / "cassettes"
    cassettes.mkdir()
    (tmp_path / "outside.json").write_text("{}", encoding="utf-8")
    mocks.write_text(
        "version: 1\nmocks:\n  - tool: lookup\n    backend: replay\n"
        "    cassette_file: ../outside\n",
        encoding="utf-8",
    )
    setup.write_text(
        "version: 1\ntarget: {kind: endpoint, url: 'http://localhost/chat'}\n"
        "policy: ./policy.yaml\nmocks: ./mocks.yaml\ncassettes: ./cassettes\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="escapes the cassette directory"):
        load_setup(setup)


def test_setup_validates_wildcard_replay_against_matching_concrete_cassette(tmp_path):
    _, mocks, setup = _files(tmp_path)
    cassettes = tmp_path / "cassettes"
    cassettes.mkdir()
    (cassettes / "lookup_customer.json").write_text("{}", encoding="utf-8")
    mocks.write_text(
        "version: 1\nmocks:\n  - tool: 'lookup_*'\n    backend: replay\n",
        encoding="utf-8",
    )
    setup.write_text(
        "version: 1\ntarget: {kind: endpoint, url: 'http://localhost/chat'}\n"
        "policy: ./policy.yaml\nmocks: ./mocks.yaml\ncassettes: ./cassettes\n",
        encoding="utf-8",
    )

    loaded = load_setup(setup)

    assert loaded.cassette_dir == cassettes


def test_setup_rejects_nested_wildcard_replay_cassette(tmp_path):
    """Setup must not accept a cassette the runtime cannot load by tool name."""
    _, mocks, setup = _files(tmp_path)
    cassettes = tmp_path / "cassettes"
    nested = cassettes / "recordings"
    nested.mkdir(parents=True)
    (nested / "lookup_customer.json").write_text("{}", encoding="utf-8")
    mocks.write_text(
        "version: 1\nmocks:\n  - tool: 'lookup_*'\n    backend: replay\n",
        encoding="utf-8",
    )
    setup.write_text(
        "version: 1\ntarget: {kind: endpoint, url: 'http://localhost/chat'}\n"
        "policy: ./policy.yaml\nmocks: ./mocks.yaml\ncassettes: ./cassettes\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="no replay cassette files match tool pattern"):
        load_setup(setup)


def test_setup_rejects_wildcard_cassette_symlink_escape(tmp_path, symlink_or_skip):
    """A wildcard must not bless a root-level symlink to a cassette outside it."""
    _, mocks, setup = _files(tmp_path)
    cassettes = tmp_path / "cassettes"
    cassettes.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text('{"sensitive": "outside the cassette root"}', encoding="utf-8")
    symlink_or_skip(cassettes / "lookup_customer.json", outside)
    mocks.write_text(
        "version: 1\nmocks:\n  - tool: 'lookup_*'\n    backend: replay\n",
        encoding="utf-8",
    )
    setup.write_text(
        "version: 1\ntarget: {kind: endpoint, url: 'http://localhost/chat'}\n"
        "policy: ./policy.yaml\nmocks: ./mocks.yaml\ncassettes: ./cassettes\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="escapes the cassette directory"):
        load_setup(setup)


def test_loaded_setup_rechecks_cassette_when_replay_reads_it(tmp_path, symlink_or_skip):
    """Replacing a validated cassette cannot bypass containment at use time."""
    policy, mocks, setup = _files(tmp_path)
    cassettes = tmp_path / "cassettes"
    cassettes.mkdir()
    cassette = cassettes / "lookup.json"
    cassette.write_text('{"safe": true}', encoding="utf-8")
    policy.write_text(
        "interactions: [{match: lookup, mode: mock}]\ndefault: {mode: block}\n",
        encoding="utf-8",
    )
    mocks.write_text(
        "version: 1\nmocks:\n  - tool: lookup\n    backend: replay\n"
        "    cassette_file: lookup\n",
        encoding="utf-8",
    )
    setup.write_text(
        "version: 1\ntarget: {kind: endpoint, url: 'http://localhost/chat'}\n"
        "policy: ./policy.yaml\nmocks: ./mocks.yaml\ncassettes: ./cassettes\n",
        encoding="utf-8",
    )
    loaded = load_setup(setup)
    outside = tmp_path / "outside.json"
    outside.write_text('{"sensitive": true}', encoding="utf-8")
    cassette.unlink()
    symlink_or_skip(cassette, outside)
    host = loaded.tool_host(tools={}, agent_id="a", session_id="case")

    with pytest.raises(MockBackendError, match="could not be read safely"):
        host.call_tool("lookup", {})


def test_setup_rejects_conflicting_setup_and_mock_cassette_directories(tmp_path):
    _, mocks, setup = _files(tmp_path)
    (tmp_path / "setup-cassettes").mkdir()
    (tmp_path / "mock-cassettes").mkdir()
    mocks.write_text(
        "version: 1\ncassette_dir: ./mock-cassettes\n"
        "mocks:\n  - tool: lookup\n    backend: replay\n",
        encoding="utf-8",
    )
    setup.write_text(
        "version: 1\ntarget: {kind: endpoint, url: 'http://localhost/chat'}\n"
        "policy: ./policy.yaml\nmocks: ./mocks.yaml\n"
        "cassettes: ./setup-cassettes\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="conflicting cassette directories"):
        load_setup(setup)


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


@pytest.mark.parametrize(
    "key",
    [
        "ACTION_MEDIATION_POLICY",
        "ACTION_MEDIATION_MOCKS",
        "ACTION_MEDIATION_CASSETTES",
        "ACTION_MEDIATION_LEDGER",
        "ASSERT_SANDBOX_OUTPUT",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "http_proxy",
        "https_proxy",
        "NO_PROXY",
        "no_proxy",
    ],
)
def test_runtime_owned_container_env_is_rejected_before_docker(
    tmp_path, monkeypatch, key
):
    policy, mocks, _ = _files(tmp_path)
    monkeypatch.setattr(sandbox_runtime, "docker_available", lambda: True)
    spec = ContainerSpec(
        image="example",
        container_port=8080,
        env={key: "attacker-controlled"},
    )

    with pytest.raises(SandboxRuntimeError, match="ASSERT-owned sandbox controls"):
        sandbox_runtime.start_container(
            spec,
            policy_path=policy,
            mocks_path=mocks,
            output_dir=tmp_path / "out",
        )


def test_target_env_cannot_override_custom_model_proxy_env(tmp_path, monkeypatch):
    policy, mocks, _ = _files(tmp_path)
    monkeypatch.setattr(sandbox_runtime, "docker_available", lambda: True)
    monkeypatch.setenv("PRIVATE_PROVIDER_AUTH", "host-only")
    spec = ContainerSpec(
        image="example",
        container_port=8080,
        env={"MODEL_ENDPOINT": "http://attacker.invalid"},
        model_proxy=ModelProxySpec(
            upstream_url="https://provider.invalid/chat",
            credential_env="PRIVATE_PROVIDER_AUTH",
            container_base_url_env="MODEL_ENDPOINT",
            container_key_env="MODEL_AUTH",
        ),
    )

    with pytest.raises(SandboxRuntimeError, match="ASSERT-owned sandbox controls"):
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
    target_run = next(
        call
        for call in calls
        if call[:4] == ("run", "-d", "--name", "assert-sandbox-deadbeef")
    )
    relay_run = next(
        call
        for call in calls
        if call[:4] == ("run", "-d", "--name", "assert-sandbox-relay-deadbeef")
    )
    target_command = " ".join(target_run)
    relay_command = " ".join(relay_run)
    assert "--read-only" in target_run
    assert "--user" in target_run and "65534:65534" in target_run
    assert "--cap-drop" in target_run and "ALL" in target_run
    assert "no-new-privileges" in target_run
    assert "ACTION_MEDIATION_LEDGER=/sandbox/output/mediation.jsonl" in target_run
    assert "ACTION_MEDIATION_CASSETTES=/sandbox/cassettes" in target_run
    assert f"{cassettes.resolve()}:/sandbox/cassettes:ro" in target_run
    network_commands = " ".join(" ".join(call) for call in calls if call[:2] == ("network", "create"))
    assert "--internal" in network_commands
    assert "bridge.inhibit_ipv4=true" in network_commands
    assert "enable_ip_masquerade=false" in network_commands
    assert "host.docker.internal" not in target_command
    assert "host.docker.internal:host-gateway" in relay_run
    assert "assert-sandbox-relay:18081" in target_command
    assert "assert-sandbox-relay:18082/v1" in target_command
    for command in (target_command, relay_command):
        assert "PRIVATE_PROVIDER_KEY" not in command
        assert "super-secret-real-value" not in command
    assert "assert-sandbox-deadbeef" in target_command
    assert handle.endpoint_url == "http://127.0.0.1:49152/chat"


def test_start_failure_cleans_target_relay_networks_and_host_proxy(tmp_path, monkeypatch):
    policy, mocks, _ = _files(tmp_path)
    calls: list[tuple[str, ...]] = []
    closed: list[str] = []

    class Server:
        def shutdown(self):
            closed.append("shutdown")

        def server_close(self):
            closed.append("close")

    monkeypatch.setattr(sandbox_runtime, "docker_available", lambda: True)
    monkeypatch.setattr(
        sandbox_runtime,
        "_start_egress_proxy",
        lambda **kwargs: (Server(), SimpleNamespace(), 9100),
    )
    def fail_wait(*args, **kwargs):
        raise SandboxRuntimeError("sandbox did not become ready")

    monkeypatch.setattr(sandbox_runtime, "_wait_http", fail_wait)

    def fake_docker(*args: str, check: bool = True):
        calls.append(args)
        if args and args[0] == "port":
            return SimpleNamespace(stdout="127.0.0.1:49152\n")
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(sandbox_runtime.secrets, "token_hex", lambda n: "deadbeef")
    monkeypatch.setattr(sandbox_runtime, "_docker", fake_docker)

    with pytest.raises(SandboxRuntimeError, match="did not become ready"):
        sandbox_runtime.start_container(
            ContainerSpec(image="example", container_port=8080),
            policy_path=policy,
            mocks_path=mocks,
            output_dir=tmp_path / "out",
        )

    assert ("rm", "-f", "assert-sandbox-deadbeef") in calls
    assert ("rm", "-f", "assert-sandbox-relay-deadbeef") in calls
    assert ("network", "rm", "assert-sandbox-net-deadbeef") in calls
    assert ("network", "rm", "assert-sandbox-relay-net-deadbeef") in calls
    assert closed == ["shutdown", "close"]


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
        relay_container="r",
        relay_network="rn",
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


def test_handle_cleanup_reports_nonzero_docker_exit_and_keeps_cleaning(tmp_path, monkeypatch):
    calls: list[tuple[str, ...]] = []
    closed: list[str] = []

    class Server:
        def shutdown(self): closed.append("shutdown")
        def server_close(self): closed.append("close")

    def nonzero_first_cleanup(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(
            returncode=1 if len(calls) == 1 else 0,
            stdout="",
            stderr="resource busy" if len(calls) == 1 else "",
        )

    monkeypatch.setattr(sandbox_runtime, "_docker", nonzero_first_cleanup)
    handle = sandbox_runtime.SandboxHandle(
        container="c",
        network="n",
        relay_container="r",
        relay_network="rn",
        endpoint_url="http://localhost/chat",
        output_dir=tmp_path,
        egress_log=tmp_path / "e",
        policy_json=tmp_path / "p",
        mocks_json=tmp_path / "m",
        egress_server=Server(),  # type: ignore[arg-type]
        egress_thread=SimpleNamespace(),  # type: ignore[arg-type]
    )

    with pytest.raises(SandboxRuntimeError, match="cleanup exited 1: resource busy"):
        handle.stop()

    assert len(calls) == 4
    assert closed == ["shutdown", "close"]


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


def test_failed_sandbox_prompt_preserves_egress_evidence(monkeypatch):
    """A timed-out target still produces a target_error row with egress evidence."""
    class Runtime:
        preserve_error_transcript = True

        def __init__(self):
            self.session_metadata: dict[str, str] = {}

        async def open(self):
            return None

        async def run_turn(self, messages):
            raise TimeoutError("target timed out")

        async def drain_pending_interaction_messages(self):
            args = {"host": "bad.example", "port": 443, "method": "CONNECT", "path": ""}
            return [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "id": "egress-1",
                        "function": "network_egress",
                        "arguments": args,
                    }],
                },
                {
                    "role": "tool",
                    "content": json.dumps({**args, "decision": "denied"}),
                    "function": "network_egress",
                    "arguments": args,
                    "tool_call_id": "egress-1",
                },
            ]

        async def close(self):
            return None

    runtime = Runtime()
    monkeypatch.setattr(inference_stage, "_build_target_session", lambda **kwargs: runtime)

    transcript = asyncio.run(inference_stage._run_prompt_test_case(
        test_case={
            "type": "prompt",
            "test_case_id": "case-1",
            "behavior": "egress",
            "seed": {"description": "try the network"},
        },
        target=TargetConfig(sandbox="setup.yaml"),
        inference=InferenceConfig(),
        max_tokens=100,
        config_path=None,
    ))

    payload = json.dumps(transcript.to_dict())
    assert transcript.stop_reason == "target_error"
    assert "bad.example" in payload
    assert "network_egress" in payload


def test_sandbox_startup_failure_propagates_instead_of_becoming_target_error(monkeypatch):
    """Docker/setup failures abort the eval; only an open runtime can yield evidence."""
    class Runtime:
        preserve_error_transcript = True

        def __init__(self):
            self.closed = False

        async def open(self):
            raise SandboxRuntimeError("Docker failed to start")

        async def close(self):
            self.closed = True

    runtime = Runtime()
    monkeypatch.setattr(inference_stage, "_build_target_session", lambda **kwargs: runtime)

    with pytest.raises(SandboxRuntimeError, match="Docker failed to start"):
        asyncio.run(inference_stage._run_prompt_test_case(
            test_case={
                "type": "prompt",
                "test_case_id": "case-startup-failure",
                "behavior": "sandbox startup",
                "seed": {"description": "try the sandbox"},
            },
            target=TargetConfig(sandbox="setup.yaml"),
            inference=InferenceConfig(),
            max_tokens=100,
            config_path=None,
        ))

    assert runtime.closed is True
