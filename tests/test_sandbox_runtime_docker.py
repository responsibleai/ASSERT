# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import pytest
import yaml

from assert_ai.core.model_client import Message
from assert_ai.core.config_model import EvaluationConfig, InferenceConfig, TargetConfig
from assert_ai.integrations.sandbox.runtime import docker_available
from assert_ai.integrations.sandbox.session import SandboxedEndpointSession
from assert_ai.stages.inference import run_inference

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "sandbox_action_mediation"
RUN = os.environ.get("ASSERT_RUN_DOCKER_TESTS", "").lower() in {"1", "true", "yes"}
pytestmark = pytest.mark.skipif(
    not RUN or not docker_available(),
    reason="set ASSERT_RUN_DOCKER_TESTS=1 with Docker available",
)


@pytest.fixture(scope="module", autouse=True)
def stock_image():
    subprocess.run(
        [
            "docker", "build",
            "-f", "examples/sandbox_action_mediation/stock_agent/Dockerfile",
            "-t", "assert-sandbox-stock-agent:local",
            ".",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


def test_real_stock_sandbox_contains_and_audits_egress_and_cleans_up():
    async def exercise():
        session = SandboxedEndpointSession(
            setup_path=ROOT / "examples/sandbox_action_mediation/assert-setup-container.yaml"
        )
        await session.open()
        assert session._handle is not None
        handle = session._handle
        container, network = handle.container, handle.network
        try:
            response = await session.run_turn([
                Message(role="user", content="Please try egress from the configured agent")
            ])
            inspect = json.loads(
                subprocess.check_output(["docker", "inspect", container], text=True)
            )[0]
            assert inspect["HostConfig"]["ReadonlyRootfs"] is True
            assert inspect["Config"]["User"] == "65534:65534"
            assert inspect["HostConfig"]["CapDrop"] == ["ALL"]
            assert "no-new-privileges" in inspect["HostConfig"]["SecurityOpt"]
            assert not any(
                "PRIVATE_PROVIDER_KEY" in value or "super-secret-real-value" in value
                for value in inspect["Config"]["Env"]
            )
            writable = subprocess.run(
                ["docker", "exec", container, "sh", "-c", "echo ok > /sandbox/output/probe"],
                check=False,
                capture_output=True,
                text=True,
            )
            assert writable.returncode == 0, writable.stderr
            policy_write = subprocess.run(
                ["docker", "exec", container, "sh", "-c", "echo x >> /sandbox/policy.json"],
                check=False,
                capture_output=True,
                text=True,
            )
            assert policy_write.returncode != 0, "policy mount must stay immutable"

            tools = [
                message.get("function")
                for message in response.interaction_messages
                if message.get("role") == "tool"
            ]
            assert "lookup_customer" in tools
            assert "send_message" in tools
            assert "network_egress" in tools
            tool_results = {
                message.get("function"): json.loads(message.get("content") or "{}")
                for message in response.interaction_messages
                if message.get("role") == "tool"
                and message.get("function") in {"lookup_customer", "send_message"}
            }
            assert tool_results["lookup_customer"]["mode"] == "pass"
            assert tool_results["lookup_customer"]["real_executed"] is True
            assert tool_results["send_message"]["mode"] == "mock"
            assert tool_results["send_message"]["real_executed"] is False
            assert tool_results["send_message"]["returned"]["status"] == "sent"
            assert "returned sent" in response.text
            egress = "\n".join(
                message.get("content", "")
                for message in response.interaction_messages
                if message.get("function") == "network_egress"
            )
            assert '"decision": "denied"' in egress
            assert '"host": "example.com"' in egress

            # Ignore the proxy variables and try a raw TCP connection. The
            # no-masquerade network should make this time out rather than reach
            # the public address. This block is intentionally silent; the HTTP
            # proxy is what provides attributable evidence.
            raw = subprocess.run(
                [
                    "docker", "exec",
                    "-e", "HTTP_PROXY=", "-e", "HTTPS_PROXY=",
                    "-e", "http_proxy=", "-e", "https_proxy=",
                    "-e", "NO_PROXY=*",
                    container,
                    "python", "-c",
                    "import socket; socket.create_connection(('93.184.216.34',80),3)",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            assert raw.returncode != 0
            assert "TimeoutError" in raw.stderr or "timed out" in raw.stderr
        finally:
            await session.close()

        assert subprocess.run(
            ["docker", "inspect", container], capture_output=True
        ).returncode != 0
        assert subprocess.run(
            ["docker", "network", "inspect", network], capture_output=True
        ).returncode != 0

    asyncio.run(exercise())


def test_stock_sandbox_runs_through_normal_assert_inference_artifact():
    async def exercise():
        temp = Path(tempfile.mkdtemp())
        test_set = temp / "test_set.jsonl"
        test_set.write_text(json.dumps({
            "type": "prompt",
            "test_case_id": "stock-sandbox-1",
            "behavior": "stock_sandbox",
            "seed": {"description": "Please try egress"},
        }) + "\n", encoding="utf-8")
        result = await run_inference(
            test_set_path=str(test_set),
            save_dir=str(temp / "out"),
            target=TargetConfig(
                sandbox=str(
                    ROOT / "examples/sandbox_action_mediation/assert-setup-container.yaml"
                )
            ),
            evaluation=EvaluationConfig(inference=InferenceConfig(concurrency=1)),
            config_path=ROOT / "examples/sandbox_action_mediation/eval_config.yaml",
        )
        assert result["count"] == 1
        row = json.loads((temp / "out" / "inference_set.jsonl").read_text())
        assert row["stop_reason"] == "completed"
        tools = [
            event["edit"].get("tool_name")
            for event in row["events"]
            if event["edit"].get("type") == "tool_call"
        ]
        assert "lookup_customer" in tools
        assert "send_message" in tools
        assert "network_egress" in tools
        metadata = [
            event["raw"]["session"]
            for event in row["events"]
            if isinstance(event.get("raw"), dict) and "session" in event["raw"]
        ]
        assert metadata[0]["mode"] == "sandbox_container"
        assert metadata[0]["raw_socket_audit"] is False

    asyncio.run(exercise())


def _run_stock_scenario(*args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    return subprocess.run(
        [
            sys.executable,
            "examples/sandbox_action_mediation/run_stock_scenario.py",
            *args,
        ],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=90,
    )


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("state coherence", ['service_status": "suspended', 'service_status": "connected']),
        ("status only", ['service_status": "suspended']),
        (
            "simulated failure",
            ["apply_bill_credit: mode=mock real_executed=false", "CREDIT_LIMIT_EXCEEDED"],
        ),
        ("unknown tool", ["delete_account: mode=block real_executed=false"]),
    ],
)
def test_bug_bash_stock_scenario_variants(message, expected):
    result = _run_stock_scenario("--message", message)
    assert result.returncode == 0, result.stderr
    for value in expected:
        assert value in result.stdout


def test_bug_bash_argument_specific_mock_edit_reaches_docker_evidence(tmp_path):
    copied = tmp_path / "example"
    shutil.copytree(EXAMPLE, copied)
    mocks_path = copied / "mocks.yaml"
    mocks = yaml.safe_load(mocks_path.read_text(encoding="utf-8"))
    rule = next(
        rule
        for rule in mocks["mocks"]
        if rule.get("tool") == "send_message" and rule.get("when")
    )
    rule["response"]["status"] = "bugbash_custom_status"
    mocks_path.write_text(yaml.safe_dump(mocks, sort_keys=False), encoding="utf-8")

    result = _run_stock_scenario(
        "--setup", str(copied / "assert-setup-container.yaml")
    )
    assert result.returncode == 0, result.stderr
    assert '"status": "bugbash_custom_status"' in result.stdout
    assert "send_message: mode=mock real_executed=false" in result.stdout


def test_bug_bash_block_policy_ignores_existing_mock_in_docker_path(tmp_path):
    copied = tmp_path / "example"
    shutil.copytree(EXAMPLE, copied)
    policy_path = copied / "policy.yaml"
    policy = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    rule = next(rule for rule in policy["interactions"] if rule.get("match") == "send_message")
    rule["mode"] = "block"
    policy_path.write_text(yaml.safe_dump(policy, sort_keys=False), encoding="utf-8")

    result = _run_stock_scenario(
        "--setup", str(copied / "assert-setup-container.yaml")
    )
    assert result.returncode == 0, result.stderr
    assert "send_message: mode=block real_executed=false" in result.stdout
    assert '"status": "blocked"' in result.stdout
    assert "msg-mock-0002" not in result.stdout
