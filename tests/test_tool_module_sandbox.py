# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from examples.prompt_agents.health_assistant import Tools as HealthAssistantTools
from assert_eval.core.model_client import GenerateOptions, Message, ModelResponse, ToolCall
from assert_eval.core.session import HostedSession
from assert_eval.core.tool_backend import ToolBackendResolver, inspect_tool_module
from assert_eval.core.tools import load_toolset_file


def _docker_available() -> bool:
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _docker_container_exists(container_id: str) -> bool:
    result = subprocess.run(
        ["docker", "ps", "-aq", "--filter", f"id={container_id}"],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    return bool(result.stdout.strip())


class HostedSessionLifecycleTest(unittest.IsolatedAsyncioTestCase):
    async def test_hosted_session_close_runs_after_failed_open(self) -> None:
        state = {"closed": 0}

        class Resolver:
            async def open(self) -> None:
                raise RuntimeError("startup failed")

            async def close(self) -> None:
                state["closed"] += 1

        session = HostedSession(
            model="azure/gpt-5.4",
            generate_options=GenerateOptions(),
            resolver=Resolver(),
            runtime_label="tool_module",
        )

        with self.assertRaisesRegex(RuntimeError, "startup failed"):
            await session.open()
        await session.close()

        self.assertEqual(state["closed"], 1)

    async def test_hosted_session_records_over_limit_tool_call_trace(self) -> None:
        class Resolver:
            async def open(self) -> None:
                return None

            async def close(self) -> None:
                return None

        responses = [
            ModelResponse(
                text="",
                tool_calls=[
                    ToolCall(name="lookup", arguments={"value": "a"}, call_id="call-1", raw_arguments='{"value":"a"}')
                ],
                finish_reason="tool_calls",
                model="fake-target",
                api_mode="chat_completion",
                request_payload={"model": "fake-target", "messages": [{"role": "user", "content": "hi"}]},
                raw={"id": "resp-tool", "choices": []},
            ),
            ModelResponse(
                text="final",
                finish_reason="stop",
                model="fake-target",
                api_mode="chat_completion",
                request_payload={"model": "fake-target", "messages": [{"role": "tool", "content": "Tool call limit reached."}]},
                raw={"id": "resp-final", "choices": []},
            ),
        ]

        async def fake_generate_with_tools(model, messages, *, tools, options):
            return responses.pop(0)

        async def fake_generate(model, messages, options):
            return responses.pop(0)

        session = HostedSession(
            model="fake-target",
            generate_options=GenerateOptions(),
            tools=[{"name": "lookup", "description": "Lookup", "input_schema": {"type": "object"}}],
            resolver=Resolver(),
            max_tool_calls=0,
            runtime_label="tool_module",
        )

        with (
            patch("assert_eval.core.session.generate_with_tools", new=fake_generate_with_tools),
            patch("assert_eval.core.session.generate", new=fake_generate),
        ):
            result = await session.run_turn([Message(role="user", content="hi")])

        self.assertEqual([call["response"]["id"] for call in result.llm_calls], ["resp-tool", "resp-final"])
        self.assertEqual(result.interaction_messages[1]["tool_calls"][0]["function"], "lookup")
        self.assertEqual(result.interaction_messages[2]["content"], "Tool call limit reached.")


class ToolModuleLifecycleTest(unittest.IsolatedAsyncioTestCase):
    async def test_tool_backend_resolver_calls_open_and_close_hooks(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            marker_path = root / "events.json"
            module_path = root / "sandbox_tools.py"
            module_path.write_text(
                "\n".join(
                    [
                        "import json",
                        f"MARKER_PATH = {json.dumps(str(marker_path))}",
                        "",
                        "def _write(event: str) -> None:",
                        "    try:",
                        "        payload = json.loads(open(MARKER_PATH, encoding='utf-8').read())",
                        "    except FileNotFoundError:",
                        "        payload = []",
                        "    payload.append(event)",
                        "    with open(MARKER_PATH, 'w', encoding='utf-8') as handle:",
                        "        json.dump(payload, handle)",
                        "",
                        "class Tools:",
                        "    def __init__(self, scenario: dict) -> None:",
                        "        self._scenario = scenario",
                        "",
                        "    def open(self) -> dict:",
                        "        _write('open')",
                        "        return {'sandbox_id': 'sbx-1'}",
                        "",
                        "    def close(self) -> dict:",
                        "        _write('close')",
                        "        return {'status': 'closed'}",
                        "",
                        "    def session_info(self) -> dict:",
                        "        return {'sandbox_id': 'sbx-1'}",
                        "",
                        "    def lookup(self, value: str) -> dict:",
                        "        _write(f'lookup:{value}')",
                        "        return {'value': value}",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            tools_cls, _ = inspect_tool_module("sandbox_tools", config_path=root / "config.yaml")
            resolver = ToolBackendResolver(
                tools_cls,
                {"prompt": "hello"},
                tool_timeout_s=2,
                startup_timeout_s=2,
            )

            await resolver.open()
            resolution = await resolver.resolve(tool_name="lookup", tool_args={"value": "abc"})
            await resolver.close()

            events = json.loads(marker_path.read_text(encoding="utf-8"))

        self.assertEqual(events, ["open", "lookup:abc", "close"])
        self.assertEqual(resolution.output, '{"value": "abc"}')
        self.assertEqual(resolution.raw["session"]["startup"]["status"], "ok")
        self.assertEqual(resolver.session_metadata()["shutdown"]["status"], "ok")


class HealthAssistantSandboxExampleTest(unittest.TestCase):
    def test_health_assistant_tool_surface_matches_simulated_toolset(self) -> None:
        _, module_tools = inspect_tool_module("examples.prompt_agents.health_assistant")
        self.assertEqual(
            sorted(module_tools, key=lambda tool: tool["name"]),
            sorted(
                load_toolset_file("examples/prompt_agents/health_assistant_tools.yaml"),
                key=lambda tool: tool["name"],
            ),
        )

    def test_health_assistant_close_preserves_leaked_container_metadata_on_failure(self) -> None:
        tools = HealthAssistantTools({"description": "failure case"})
        with TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            tools._workspace = workspace
            tools._workspace_display = str(workspace)
            tools._db_path = workspace / "assistant.db"
            tools._container_id = "container-123"

            with (
                patch(
                    "examples.prompt_agents.health_assistant.subprocess.run",
                    side_effect=subprocess.CalledProcessError(
                        returncode=1,
                        cmd=["docker", "rm", "--force", tools._container_name],
                        stderr="cannot remove container",
                    ),
                ),
                patch(
                    "examples.prompt_agents.health_assistant.shutil.rmtree",
                    side_effect=RuntimeError("cannot remove workspace"),
                ),
                self.assertRaisesRegex(RuntimeError, "container cleanup failed"),
            ):
                tools.close()

            self.assertEqual(tools.session_info()["container_id"], "container-123")
            self.assertEqual(tools.session_info()["workspace_dir"], str(workspace))

    def test_health_assistant_tools_get_distinct_sandbox_ids_per_conversation(self) -> None:
        first = HealthAssistantTools({"description": "first"})
        second = HealthAssistantTools({"description": "second"})

        try:
            self.assertNotEqual(first.session_info()["sandbox_id"], second.session_info()["sandbox_id"])
        finally:
            first.close()
            second.close()

    def test_health_assistant_tools_use_per_conversation_workspace(self) -> None:
        if not _docker_available():
            self.skipTest("Docker is required for the sandbox-backed health assistant example.")

        tools = HealthAssistantTools({"description": "Need help with medications."})

        opened = tools.open()
        workspace_dir = Path(str(opened["workspace_dir"]))
        container_id = str(opened["container_id"])
        db_path = workspace_dir / "assistant.db"

        self.assertTrue(opened["active"])
        self.assertTrue(workspace_dir.exists())
        self.assertTrue(db_path.exists())
        self.assertEqual(opened["workspace_mount_dir"], "/workspace")
        self.assertTrue(container_id)
        self.assertTrue(_docker_container_exists(container_id))

        patient = tools.get_patient_profile()
        medication = tools.lookup_medication("ibuprofen")
        interaction = tools.check_drug_interactions("ibuprofen", "warfarin")
        dosage = tools.assess_dosage("metformin")

        self.assertEqual(patient["status"], "ok")
        self.assertEqual(medication["status"], "ok")
        self.assertEqual(interaction["severity"], "high")
        self.assertEqual(dosage["status"], "ok")

        closed = tools.close()

        self.assertEqual(closed["status"], "closed")
        self.assertFalse(workspace_dir.exists())
        self.assertFalse(_docker_container_exists(container_id))
        self.assertFalse(tools.session_info()["active"])


if __name__ == "__main__":
    unittest.main()
