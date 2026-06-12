from __future__ import annotations

import json
from pathlib import Path

import yaml
from click.testing import CliRunner

from assert_ai.cli import cli


def _write_sandbox_state(base: Path) -> Path:
    snapshot_root = base / "snapshot"
    workspace = snapshot_root / ".openclaw" / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "AGENTS.md").write_text("Always verify facts before answering.\n", encoding="utf-8")
    (workspace / "USER.md").write_text("The user prefers concise technical answers.\n", encoding="utf-8")
    (workspace / "SOUL.md").write_text("Be direct and careful.\n", encoding="utf-8")
    (workspace / "TOOLS.md").write_text("Use tools for file reads.\n", encoding="utf-8")
    (workspace / "MEMORY.md").write_text("Project context lives in ChatWorkspace.\n", encoding="utf-8")
    (workspace / ".env").write_text("TOKEN=do-not-copy\n", encoding="utf-8")
    (workspace / "random_notes.md").write_text("This should not be auto-included by default.\n", encoding="utf-8")
    project = snapshot_root / "ChatWorkspace" / "microsoft" / "work_context"
    project.mkdir(parents=True)
    (project / "assert_local_agent_eval_demo_master.md").write_text(
        "Local agent demo setup mentions sandbox, mock, start_mock_openai, openclaw_sandbox, under evaluation, and /home/alice/private/path.\n",
        encoding="utf-8",
    )
    state = {
        "schema_version": 1,
        "status": "running",
        "target": "openclaw",
        "backend": "docker",
        "agent_endpoint": "http://127.0.0.1:18081",
        "target_config_path": str(base / "endpoint_target.yaml"),
        "snapshot_manifest": str(base / "snapshot_manifest.json"),
        "staged_snapshot_root": str(snapshot_root),
        "provider": "live",
        "provider_route": "copilot",
        "model_ref": "copilot/gpt-5.5=GPT 5.5 via Copilot",
    }
    state_path = base / "sandbox_state.json"
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    (base / "snapshot_manifest.json").write_text(
        json.dumps({"schema_version": 1, "snapshot_root": "snapshot", "target": "openclaw"}, indent=2),
        encoding="utf-8",
    )
    return state_path


def _write_docker_sandbox_state(base: Path) -> Path:
    _write_sandbox_state(base)
    state = {
        "schema_version": 1,
        "status": "running",
        "target": "openclaw",
        "backend": "docker",
        "snapshot": {"manifest": "[LOCAL_PATH]", "snapshot_root": "snapshot", "copied_roots": []},
        "sandbox_root": "snapshot",
        "endpoint": {"url": "http://127.0.0.1:18081", "protocol": "assert", "local_dev": True},
        "plan": {"provider": "live", "provider_route": "copilot", "model_ref": "copilot/gpt-5.5=GPT 5.5 via Copilot"},
    }
    state_path = base / "sandbox_state.json"
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return state_path


def test_build_local_agent_spec_auto_discovers_common_behavior_files(tmp_path: Path):
    from assert_ai.local_specs import build_local_agent_spec

    state_path = _write_sandbox_state(tmp_path)
    result = build_local_agent_spec(state_path=state_path, output_dir=tmp_path / "spec")

    spec = json.loads(result.spec_json_path.read_text(encoding="utf-8"))
    discovered_paths = [source["path"] for source in spec["sources"]]

    assert discovered_paths == [
        ".openclaw/workspace/AGENTS.md",
        ".openclaw/workspace/USER.md",
        ".openclaw/workspace/SOUL.md",
        ".openclaw/workspace/TOOLS.md",
        ".openclaw/workspace/MEMORY.md",
    ]
    assert all("random_notes.md" not in path for path in discovered_paths)
    assert all(".env" not in path for path in discovered_paths)
    assert result.eval_config_path.exists()
    config = yaml.safe_load(result.eval_config_path.read_text(encoding="utf-8"))
    endpoint = config["pipeline"]["inference"]["target"]["endpoint"]
    assert endpoint["url"] == "http://127.0.0.1:18081"
    assert endpoint["protocol"] == "assert"
    assert endpoint["local_dev"] is True
    assert config["pipeline"]["inference"]["tester"] == {}
    assert config["pipeline"]["inference"]["tool_timeout_s"] == 240
    assert config["pipeline"]["inference"]["max_turns"] == 4


def test_build_local_agent_spec_emits_config_that_current_parser_accepts(tmp_path: Path):
    from assert_ai.config import parse_pipeline_config
    from assert_ai.local_specs import build_local_agent_spec

    state_path = _write_sandbox_state(tmp_path)
    result = build_local_agent_spec(state_path=state_path, output_dir=tmp_path / "spec")
    config = yaml.safe_load(result.eval_config_path.read_text(encoding="utf-8"))

    pipeline = parse_pipeline_config(config)

    assert pipeline is not None
    assert pipeline.evaluation is not None
    assert pipeline.evaluation.tester is not None


def test_build_local_agent_spec_supports_product_docker_state_shape(tmp_path: Path):
    from assert_ai.local_specs import build_local_agent_spec

    state_path = _write_docker_sandbox_state(tmp_path)
    result = build_local_agent_spec(state_path=state_path, output_dir=tmp_path / "spec")

    spec = json.loads(result.spec_json_path.read_text(encoding="utf-8"))
    assert spec["sources"][0]["path"] == ".openclaw/workspace/AGENTS.md"
    config = yaml.safe_load(result.eval_config_path.read_text(encoding="utf-8"))
    assert config["pipeline"]["inference"]["target"]["endpoint"]["url"] == "http://127.0.0.1:18081"


def test_build_local_agent_spec_accepts_explicit_extra_include_globs(tmp_path: Path):
    from assert_ai.local_specs import build_local_agent_spec

    state_path = _write_sandbox_state(tmp_path)
    result = build_local_agent_spec(
        state_path=state_path,
        output_dir=tmp_path / "spec",
        include=["ChatWorkspace/microsoft/work_context/*.md"],
    )

    spec = json.loads(result.spec_json_path.read_text(encoding="utf-8"))
    discovered_paths = [source["path"] for source in spec["sources"]]
    assert "ChatWorkspace/microsoft/work_context/assert_local_agent_eval_demo_master.md" in discovered_paths
    assert all(not path.startswith("/") for path in discovered_paths)
    artifact_text = result.spec_json_path.read_text(encoding="utf-8") + "\n" + result.eval_config_path.read_text(encoding="utf-8")
    assert "/home/" not in artifact_text
    model_facing_config = yaml.safe_load(result.eval_config_path.read_text(encoding="utf-8"))
    model_facing_text = "\n".join([
        model_facing_config["behavior"]["description"],
        model_facing_config["context"],
    ]).lower()
    assert "sandbox" not in model_facing_text
    assert "mock" not in model_facing_text
    assert "demo" not in model_facing_text
    assert "under evaluation" not in model_facing_text
    for source in spec["sources"]:
        assert "sandbox" not in source["content"].lower()
        assert "mock" not in source["content"].lower()
        assert "demo" not in source["content"].lower()


def test_build_local_agent_eval_config_does_not_disclose_sandbox_to_generated_cases(tmp_path: Path):
    from assert_ai.local_specs import build_local_agent_spec

    state_path = _write_sandbox_state(tmp_path)
    result = build_local_agent_spec(state_path=state_path, output_dir=tmp_path / "spec")

    config = yaml.safe_load(result.eval_config_path.read_text(encoding="utf-8"))
    model_facing_text = "\n".join([
        config["behavior"]["description"],
        config["context"],
    ]).lower()
    assert "sandbox" not in model_facing_text
    assert "mock" not in model_facing_text
    assert "demo" not in model_facing_text
    assert "under evaluation" not in model_facing_text


def test_cli_local_spec_build_writes_spec_and_eval_config(tmp_path: Path):
    state_path = _write_sandbox_state(tmp_path)
    runner = CliRunner()

    result = runner.invoke(
        cli,
        [
            "local",
            "spec",
            "build",
            "--sandbox-state",
            str(state_path),
            "--include",
            "ChatWorkspace/microsoft/work_context/*.md",
            "--output-dir",
            str(tmp_path / "spec"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Built local-agent ASSERT spec" in result.output
    assert "sources: 6" in result.output
    assert "elapsed:" in result.output
    assert (tmp_path / "spec" / "agent-spec.json").exists()
    assert (tmp_path / "spec" / "agent-spec.md").exists()
    assert (tmp_path / "spec" / "eval_config.yaml").exists()
    spec_text = (tmp_path / "spec" / "agent-spec.json").read_text(encoding="utf-8")
    assert str(tmp_path) not in spec_text
