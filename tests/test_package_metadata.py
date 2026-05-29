import tomllib
from pathlib import Path


def test_console_script_aliases_are_backward_compatible() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert pyproject["project"]["scripts"] == {
        "assert-eval": "assert_eval.cli:cli",
        "assert": "assert_eval.cli:cli",
        "p2m": "assert_eval.cli:cli",
    }
