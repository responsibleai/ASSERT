import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from p2m.config import load_runtime_context, parse_pipeline_config
from p2m.core.tool_backend import inspect_tool_module
from p2m.core.model_client import Message
from p2m.core.model_client import GenerateOptions
from p2m.core.session import ExternalSession
from p2m.stages.rollout import _build_hosted_session as build_hosted_session


class ConfigAndHandlerFoundationTest(unittest.TestCase):
    def test_parse_pipeline_config_rejects_old_target_fields_and_missing_target(self) -> None:
        with self.assertRaisesRegex(ValueError, "pipeline.rollout.target has unsupported field\\(s\\): kind"):
            parse_pipeline_config(
                {"pipeline": {"rollout": {"target": {"kind": "unknown", "model": {"name": "azure/gpt-5.4"}}}}},
                Path("config.yaml"),
            )

        with self.assertRaisesRegex(ValueError, "pipeline.rollout.target has unsupported field\\(s\\): toolset"):
            parse_pipeline_config(
                {"pipeline": {"rollout": {"target": {"model": {"name": "azure/gpt-5.4"}, "toolset": "tools.yaml"}}}},
                Path("config.yaml"),
            )

        with self.assertRaisesRegex(ValueError, "pipeline.rollout.target has unsupported field\\(s\\): simulator"):
            parse_pipeline_config(
                {"pipeline": {"rollout": {"target": {"model": {"name": "azure/gpt-5.4"}, "simulator": "azure/gpt-5.4"}}}},
                Path("config.yaml"),
            )

        with self.assertRaisesRegex(ValueError, "target requires exactly one of 'model', 'connector', or 'callable'"):
            parse_pipeline_config({"pipeline": {"rollout": {"target": {}}}}, Path("config.yaml"))

        with self.assertRaisesRegex(ValueError, "pipeline.rollout.environment is no longer supported"):
            parse_pipeline_config(
                {
                    "pipeline": {
                        "rollout": {
                            "target": {"model": {"name": "azure/gpt-5.4"}},
                            "environment": {"backend": "weird"},
                        }
                    },
                },
                Path("config.yaml"),
            )

    def test_parse_pipeline_config_validates_target_tools_shape(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "target.tools.toolset requires target.tools.simulator",
        ):
            parse_pipeline_config(
                {
                    "pipeline": {
                        "rollout": {
                            "target": {
                                "model": {"name": "azure/gpt-5.4"},
                                "tools": {"toolset": "tools.yaml"},
                            },
                        }
                    },
                },
                Path("config.yaml"),
            )

        with self.assertRaisesRegex(ValueError, "target.tools must define module or toolset\\+simulator"):
            parse_pipeline_config(
                {
                    "pipeline": {
                        "rollout": {
                            "target": {
                                "model": {"name": "azure/gpt-5.4"},
                                "tools": {},
                            },
                        }
                    },
                },
                Path("config.yaml"),
            )

        with self.assertRaisesRegex(ValueError, "target.tools.module and target.tools.toolset are mutually exclusive"):
            parse_pipeline_config(
                {
                    "pipeline": {
                        "rollout": {
                            "target": {
                                "model": {"name": "azure/gpt-5.4"},
                                "tools": {"module": "examples.agents.health_assistant", "toolset": "tools.yaml"},
                            },
                        }
                    },
                },
                Path("config.yaml"),
            )

    def test_parse_pipeline_config_rejects_conflicting_target_modes(self) -> None:
        with self.assertRaisesRegex(ValueError, "target requires exactly one of 'model', 'connector', or 'callable'"):
            parse_pipeline_config(
                {
                    "pipeline": {
                        "rollout": {
                            "target": {
                                "model": {"name": "azure/gpt-5.4"},
                                "connector": "examples.agents.demo",
                            }
                        }
                    }
                },
                Path("config.yaml"),
            )

        with self.assertRaisesRegex(ValueError, "external target must not define target.tools"):
            parse_pipeline_config(
                {
                    "pipeline": {
                        "rollout": {
                            "target": {
                                "connector": "examples.agents.demo",
                                "tools": {"module": "examples.agents.health_assistant"},
                            }
                        }
                    }
                },
                Path("config.yaml"),
            )

    def test_load_runtime_context_reads_rollout_target(self) -> None:
        context = load_runtime_context(
            {
                "suite_id": "suite-v1",
                "pipeline": {
                    "rollout": {
                        "target": {
                            "model": {"name": "azure/gpt-5.4"},
                            "tools": {"module": "examples.agents.health_assistant"},
                        },
                        "seed_path": "examples/agents/health_assistant_tools.yaml",
                    }
                },
            },
            Path("examples/pipes/health_assistant.yaml"),
            stage_modules={"rollout": type("Stage", (), {"SCOPE": "run"})()},
        )

        self.assertEqual(context["target"].model, "azure/gpt-5.4")
        self.assertEqual(context["target"].tools.module, "examples.agents.health_assistant")
        self.assertNotIn("environment", context)

    def test_parse_pipeline_config_keeps_system_prompt_as_literal_text(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            prompt_path = Path(tmp_dir) / "prompt.txt"
            prompt_path.write_text("loaded from disk", encoding="utf-8")

            parsed = parse_pipeline_config(
                {
                    "pipeline": {
                        "rollout": {
                            "target": {
                                "model": {"name": "azure/gpt-5.4"},
                                "system_prompt": str(prompt_path),
                            }
                        }
                    }
                },
                Path("config.yaml"),
            )

        assert parsed is not None
        assert parsed.target is not None
        self.assertEqual(parsed.target.system_prompt, str(prompt_path))

    def test_eval_stage_normalize_revalidates_target_relationships(self) -> None:
        from p2m.core.config_model import PipelineConfig, TargetConfig, ToolsConfig

        with self.assertRaisesRegex(ValueError, "external target must not define target.tools"):
            PipelineConfig(
                target=TargetConfig(
                    connector="examples.agents.demo",
                    tools=ToolsConfig(module="examples.agents.health_assistant"),
                ),
            )

        with self.assertRaisesRegex(
            ValueError,
            "target.tools.toolset requires target.tools.simulator",
        ):
            ToolsConfig(toolset="tools.yaml")

    def test_build_hosted_session_requires_synthetic_simulator(self) -> None:
        with self.assertRaisesRegex(ValueError, "simulated tools require target.tools.simulator"):
            build_hosted_session(
                model="azure/gpt-5.4",
                tools_config={"toolset": "tools.yaml"},
                scenario={"prompt": "hello", "tools": []},
                generate_options=GenerateOptions(),
                max_tool_calls=3,
                synthetic_prompt_template="template",
            )

    def test_inspect_tool_module_loads_config_relative_package(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            config_path = root / "config.yaml"
            package_dir = root / "tool_modules"
            package_dir.mkdir()
            (package_dir / "__init__.py").write_text("", encoding="utf-8")
            (package_dir / "health_assistant.py").write_text(
                "\n".join(
                    [
                        "class Tools:",
                        "    def __init__(self, scenario: dict) -> None:",
                        "        self._scenario = scenario",
                        "",
                        "    def lookup(self, account_id: str, include_history: bool = False) -> dict:",
                        "        \"\"\"Fetch account data.",
                        "",
                        "        Args:",
                        "            account_id: Customer account identifier.",
                        "            include_history: Whether to include recent events.",
                        "        \"\"\"",
                        "        return {'account_id': account_id, 'history': include_history}",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            config_path.write_text(
                "pipeline:\n  rollout:\n    target:\n      model: azure/gpt-5.4\n",
                encoding="utf-8",
            )

            _, tools = inspect_tool_module("tool_modules.health_assistant", config_path=config_path)

        self.assertEqual(
            tools,
            [
                {
                    "name": "lookup",
                    "description": "Fetch account data.",
                    "input_schema": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "account_id": {
                                "type": "string",
                                "description": "Customer account identifier.",
                            },
                            "include_history": {
                                "type": "boolean",
                                "description": "Whether to include recent events.",
                            },
                        },
                        "required": ["account_id"],
                    },
                }
            ],
        )

    def test_inspect_tool_module_rejects_unsupported_tuple_parameters(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            module_path = root / "bad_handler.py"
            module_path.write_text(
                "\n".join(
                    [
                        "class Tools:",
                        "    def __init__(self, scenario: dict) -> None:",
                        "        self._scenario = scenario",
                        "",
                        "    def lookup(self, pair: tuple[str, str]) -> str:",
                        "        return ''",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "tuple parameters are not supported"):
                inspect_tool_module("bad_handler", config_path=root / "config.yaml")

    def test_inspect_tool_module_ignores_lifecycle_methods(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            module_path = root / "lifecycle_tools.py"
            module_path.write_text(
                "\n".join(
                    [
                        "class Tools:",
                        "    def __init__(self, scenario: dict) -> None:",
                        "        self._scenario = scenario",
                        "",
                        "    def open(self) -> None:",
                        "        return None",
                        "",
                        "    def close(self) -> None:",
                        "        return None",
                        "",
                        "    def session_info(self) -> dict:",
                        "        return {}",
                        "",
                        "    def lookup(self, account_id: str) -> dict:",
                        "        return {'account_id': account_id}",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            _, tools = inspect_tool_module("lifecycle_tools", config_path=root / "config.yaml")

        self.assertEqual([tool["name"] for tool in tools], ["lookup"])


class ExternalSessionTest(unittest.IsolatedAsyncioTestCase):
    async def test_external_session_always_sends_visible_history(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            seen_path = root / "seen.json"
            (root / "system_prompt_connector.py").write_text(
                "\n".join(
                    [
                        "import json",
                        f"SEEN_PATH = {json.dumps(str(seen_path))}",
                        "",
                        "class Adapter:",
                        "    def __init__(self, scenario: dict) -> None:",
                        "        self.scenario = scenario",
                        "",
                        "    def send_message(self, text: str, *, history=None):",
                        "        with open(SEEN_PATH, 'w', encoding='utf-8') as handle:",
                        "            json.dump({'text': text, 'history': history}, handle)",
                        "        return {'text': 'reply'}",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            session = ExternalSession(
                connector_ref="system_prompt_connector",
                scenario={"prompt": "hello"},
                startup_timeout_s=None,
                message_timeout_s=None,
                config_path=root / "config.yaml",
            )
            await session.run_turn(
                [
                    Message(role="system", content="system"),
                    Message(role="user", content="hello"),
                ]
            )

            seen = json.loads(seen_path.read_text(encoding="utf-8"))

        self.assertEqual(session.runtime_mode, "external")
        self.assertEqual([item["role"] for item in seen["history"]], ["system", "user"])

    async def test_external_session_records_visible_exchange_when_connector_returns_events(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            (root / "demo_connector.py").write_text(
                "\n".join(
                    [
                        "from p2m.core.session import ConnectorResponse",
                        "",
                        "class Adapter:",
                        "    def __init__(self, scenario: dict) -> None:",
                        "        self.scenario = scenario",
                        "",
                        "    def send_message(self, text: str, *, history=None):",
                        "        return ConnectorResponse(",
                        "            text='done',",
                        "            events=[",
                        "                {'role': 'tool_call', 'tool_name': 'lookup', 'tool_args': {'query': text}, 'tool_call_id': 'tc-1', 'content': ''},",
                        "                {'role': 'tool_result', 'tool_name': 'lookup', 'tool_args': {'query': text}, 'tool_call_id': 'tc-1', 'content': 'ok'},",
                        "                {'role': 'assistant', 'content': 'done'},",
                        "            ],",
                        "            raw={'connector': 'demo'},",
                        "        )",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            session = ExternalSession(
                connector_ref="demo_connector",
                scenario={"prompt": "hello"},
                startup_timeout_s=None,
                message_timeout_s=None,
                config_path=root / "config.yaml",
            )
            result = await session.run_turn([Message(role="user", content="hello")])

        self.assertEqual(session.runtime_mode, "external")
        self.assertEqual(result.interaction_messages[0]["role"], "user")
        self.assertEqual(result.interaction_messages[0]["content"], "hello")
        self.assertEqual(result.interaction_messages[1]["tool_calls"][0]["function"], "lookup")
        self.assertEqual(result.interaction_messages[-1]["content"], "done")

    async def test_external_session_passes_full_history_and_latest_user_text(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            seen_path = root / "seen.json"
            (root / "history_connector.py").write_text(
                "\n".join(
                    [
                        "import json",
                        f"SEEN_PATH = {json.dumps(str(seen_path))}",
                        "",
                        "class Adapter:",
                        "    def __init__(self, scenario: dict) -> None:",
                        "        self.scenario = scenario",
                        "",
                        "    def send_message(self, text: str, *, history=None):",
                        "        with open(SEEN_PATH, 'w', encoding='utf-8') as handle:",
                        "            json.dump({'text': text, 'history': history}, handle)",
                        "        return {'text': 'reply', 'raw': {'connector': 'history'}}",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            session = ExternalSession(
                connector_ref="history_connector",
                scenario={"prompt": "hello"},
                startup_timeout_s=None,
                message_timeout_s=None,
                config_path=root / "config.yaml",
            )
            await session.run_turn(
                [
                    Message(role="system", content="system"),
                    Message(role="user", content="hello"),
                ]
            )

            seen = json.loads(seen_path.read_text(encoding="utf-8"))

        self.assertEqual(seen["text"], "hello")
        self.assertEqual([item["role"] for item in seen["history"]], ["system", "user"])
