from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "setup_clarity.py"


def load_module():
    spec = importlib.util.spec_from_file_location("setup_clarity_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_requirement_is_pinned_and_includes_mcp() -> None:
    module = load_module()

    assert module.CLARITY_VERSION.startswith("v")
    assert "@v0.1.4" in module.CLARITY_REQUIREMENT
    assert "clarity-agent[mcp]" in module.CLARITY_REQUIREMENT


def test_cache_is_user_scoped_not_workspace_scoped() -> None:
    module = load_module()

    root = module.cache_root()
    assert "assert-ai" in root.parts
    assert "clarity" in root.parts
    assert module.CLARITY_VERSION in root.parts
    assert ".claude" not in root.parts


def test_cache_can_be_overridden_for_managed_hosts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load_module()
    monkeypatch.setenv("ASSERT_CLARITY_CACHE", str(tmp_path))

    assert module.cache_root() == tmp_path


def test_venv_python_is_platform_specific(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_module()

    monkeypatch.setattr(module.sys, "platform", "win32")
    assert module.venv_python(tmp_path) == tmp_path / "Scripts" / "python.exe"

    monkeypatch.setattr(module.sys, "platform", "linux")
    assert module.venv_python(tmp_path) == tmp_path / "bin" / "python"


def test_verify_requires_pip_mode_mcp_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load_module()
    python = tmp_path / "tool" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.touch()
    config = tmp_path / ".vscode" / "mcp.json"
    config.parent.mkdir()
    config.write_text(
        json.dumps(
            {
                "servers": {
                    "clarity-agent": {
                        "type": "stdio",
                        "command": str(python),
                        "args": ["-m", "clarity_agent.mcp"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    module.verify_mcp_config(tmp_path, python)

    assert calls == [[str(python), "-m", "clarity_agent.mcp", "--help"]]


def test_verify_rejects_uv_checkout_mode(tmp_path: Path) -> None:
    module = load_module()
    python = tmp_path / "tool" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.touch()
    config = tmp_path / ".vscode" / "mcp.json"
    config.parent.mkdir()
    config.write_text(
        json.dumps(
            {
                "servers": {
                    "clarity-agent": {
                        "type": "stdio",
                        "command": "uv",
                        "args": [
                            "run",
                            "--extra",
                            "mcp",
                            "--directory",
                            "C:/another/checkout",
                        ],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="pip-mode MCP config"):
        module.verify_mcp_config(tmp_path, python)


def test_embed_removes_nonfunctional_wrappers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load_module()
    python = tmp_path / "venv" / "Scripts" / "python.exe"
    project = tmp_path / "project"
    project.mkdir()
    for name in ("clarity", "clarity.bat", "clarity.ps1"):
        (project / name).write_text("dead wrapper", encoding="utf-8")

    monkeypatch.setattr(module, "_run", lambda *args, **kwargs: None)
    module.embed_project(python, project)

    assert not any((project / name).exists() for name in ("clarity", "clarity.bat", "clarity.ps1"))
