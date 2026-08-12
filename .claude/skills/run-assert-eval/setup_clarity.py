"""Install and embed Clarity without a second source checkout.

Clarity is not currently published to PyPI, requires Python 3.12 while ASSERT
supports 3.11, and does not install a ``clarity`` console command. Making it an
ASSERT core dependency (or a normal extra) would therefore either raise ASSERT's
Python floor or leave users with a command the package does not provide.

This bootstrap keeps Clarity isolated in a user-cache tool environment, installs
one pinned release directly from GitHub, and calls the installed package's embed
function. The generated MCP config points at that tool environment's Python
executable, so it does not need a second checkout, a globally installed ``uv``,
or a non-existent ``clarity`` command.

Usage:

    python .claude/skills/run-assert-eval/setup_clarity.py .

The first install is large because Clarity currently bundles every supported LLM
provider. It may take 5-10 minutes. Later workspaces reuse the same cached tool
environment and usually embed in under a second.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import venv
from pathlib import Path


CLARITY_VERSION = "v0.1.4"
CLARITY_REQUIREMENT = (
    "clarity-agent[mcp] @ "
    f"git+https://github.com/microsoft/clarity-agent.git@{CLARITY_VERSION}"
)


def cache_root() -> Path:
    """Return a per-user, cross-workspace cache for the pinned Clarity tool."""

    if override := os.environ.get("ASSERT_CLARITY_CACHE"):
        return Path(override).expanduser()
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "assert-ai" / "tools" / "clarity" / CLARITY_VERSION


def venv_python(environment: Path) -> Path:
    return environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def _run(command: list[str], *, label: str) -> None:
    print(f"==> {label}", flush=True)
    subprocess.run(command, check=True)


def install_tool(environment: Path, *, refresh: bool) -> Path:
    """Create or refresh the isolated Clarity tool environment."""

    python = venv_python(environment)
    if refresh and environment.exists():
        shutil.rmtree(environment)
    if not python.is_file():
        print(
            "==> Installing Clarity once in the ASSERT user cache. "
            "This currently downloads every provider and may take 5-10 minutes.",
            flush=True,
        )
        started = time.perf_counter()
        venv.EnvBuilder(with_pip=True).create(environment)
        python = venv_python(environment)
        _run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                CLARITY_REQUIREMENT,
            ],
            label=f"Install Clarity {CLARITY_VERSION}",
        )
        print(f"==> Clarity installed in {time.perf_counter() - started:.1f}s", flush=True)
    else:
        print(f"==> Reusing cached Clarity {CLARITY_VERSION}: {environment}", flush=True)
    return python


def embed_project(python: Path, project_dir: Path) -> None:
    """Call Clarity's installed embed function in pip mode."""

    code = (
        "import sys\n"
        "from pathlib import Path\n"
        "from clarity_agent.setup.installer import Outcome\n"
        "from clarity_agent.setup.project import run_project_embed\n"
        "results = run_project_embed(Path(sys.argv[1]), Path(sys.prefix))\n"
        "for result in results:\n"
        "    print(f'  {result.outcome.name}: {result.message}')\n"
        "if any(result.outcome == Outcome.FAIL for result in results):\n"
        "    raise SystemExit(1)\n"
    )
    _run(
        [str(python), "-c", code, str(project_dir)],
        label=f"Embed Clarity into {project_dir}",
    )

    # The installed Clarity package has no console entry point. Its generated
    # wrappers therefore only say "Clarity is not installed" and its printed
    # `.\clarity web .` next step cannot work. The skill needs MCP only, so remove
    # those success-shaped dead ends.
    for wrapper in ("clarity", "clarity.bat", "clarity.ps1"):
        (project_dir / wrapper).unlink(missing_ok=True)


def verify_mcp_config(project_dir: Path, python: Path) -> None:
    """Require a pip-mode MCP config that uses the cached tool environment."""

    config_path = project_dir / ".vscode" / "mcp.json"
    data = json.loads(config_path.read_text(encoding="utf-8"))
    server = data.get("servers", {}).get("clarity-agent", {})
    configured = Path(str(server.get("command", ""))).resolve()
    expected = python.resolve()
    if configured != expected:
        raise RuntimeError(
            "Clarity embed did not produce the expected pip-mode MCP config: "
            f"configured={configured!s}, expected={expected!s}"
        )
    subprocess.run(
        [str(python), "-m", "clarity_agent.mcp", "--help"],
        check=True,
        stdout=subprocess.DEVNULL,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "project_dir",
        nargs="?",
        type=Path,
        default=Path.cwd(),
        help="Workspace to embed Clarity into (default: current directory).",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help=f"Rebuild the cached {CLARITY_VERSION} tool environment.",
    )
    args = parser.parse_args(argv)

    if sys.version_info < (3, 12):
        parser.error(
            "Clarity requires Python 3.12+. Run this bootstrap with a Python "
            "3.12 interpreter; ASSERT itself may continue using Python 3.11."
        )

    project_dir = args.project_dir.resolve()
    if not (project_dir / ".git").exists():
        parser.error(f"not a git repository: {project_dir}")

    environment = cache_root() / "venv"
    python = install_tool(environment, refresh=args.refresh)
    embed_project(python, project_dir)
    verify_mcp_config(project_dir, python)

    print()
    print("Clarity MCP setup is ready.")
    print("1. Reload MCP servers in your IDE.")
    print("2. Confirm the `run_clarity` tool is visible.")
    print("3. Start with the five-case ASSERT smoke run before the full baseline.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
