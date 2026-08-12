from __future__ import annotations

import json

from click.testing import CliRunner

from assert_ai import cli as cli_module


def test_echo_json_is_ascii_safe_on_windows_consoles(monkeypatch) -> None:
    emitted: list[str] = []
    monkeypatch.setattr(cli_module.click, "echo", emitted.append)

    cli_module._echo_json({"route": "triage → research", "label": "permissible"})

    [text] = emitted
    text.encode("ascii")
    assert "\\u2192" in text
    assert json.loads(text) == {
        "route": "triage → research",
        "label": "permissible",
    }


def test_json_summary_removes_raw_rows_recursively() -> None:
    payload = {
        "prompt_rows": [{"large": "payload"}],
        "prompt_metrics": {"rate": 0.5},
        "runs": [
            {
                "scenario_rows": [{"large": "payload"}],
                "scenario_metrics": {"rate": 0.25},
            }
        ],
    }

    assert cli_module._json_summary(payload) == {
        "prompt_metrics": {"rate": 0.5},
        "runs": [{"scenario_metrics": {"rate": 0.25}}],
    }


def test_summary_only_requires_json() -> None:
    result = CliRunner().invoke(
        cli_module.cli,
        ["results", "status", "missing-suite", "--summary-only"],
    )

    assert result.exit_code == 1
    assert "--summary-only requires --json" in result.output
