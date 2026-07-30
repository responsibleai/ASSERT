# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Keypress-driven command runner for the five-minute standup demo."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SETUP = HERE / "assert-setup-container.yaml"
RUNNER = HERE / "run_stock_scenario.py"


def wait_for_command(enabled: bool, display: str) -> None:
    print("\n" + "─" * 76)
    print("Press SPACE or ENTER to run:")
    print(f"  {display}", flush=True)
    if not enabled:
        return
    if not sys.stdin.isatty():
        input()
        return

    try:
        import termios
        import tty

        fd = sys.stdin.fileno()
        previous = termios.tcgetattr(fd)
        try:
            tty.setcbreak(fd)
            while True:
                key = sys.stdin.read(1)
                if key in {" ", "\r", "\n"}:
                    break
                if key in {"q", "Q", "\x03"}:
                    raise KeyboardInterrupt
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, previous)
        print(flush=True)
    except (ImportError, OSError):
        input()


def run(*args: str) -> None:
    subprocess.run(list(args), check=True, cwd=ROOT)


def docker_resource_count(kind: str, name_filter: str) -> int:
    command = ["docker", kind, "ls"]
    if kind == "container":
        command.append("-a")
    command.extend(["-q", "--filter", f"name={name_filter}"])
    result = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
    )
    return len([line for line in result.stdout.splitlines() if line.strip()])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--no-pause",
        action="store_true",
        help="run continuously for preflight/automation instead of waiting for keys",
    )
    args = parser.parse_args()
    interactive = not args.no_pause

    try:
        validate_display = (
            "python -m assert_ai.integrations.sandbox.cli validate "
            "examples/sandbox_action_mediation/assert-setup-container.yaml"
        )
        wait_for_command(interactive, validate_display)
        run(
            sys.executable,
            "-m",
            "assert_ai.integrations.sandbox.cli",
            "validate",
            str(SETUP),
        )

        resolve_display = (
            "python -m assert_ai.integrations.sandbox.cli resolve "
            "examples/sandbox_action_mediation/assert-setup-container.yaml "
            "send_message --args "
            "'{\"recipient\":\"555-000-9999\",\"channel\":\"sms\"}'"
        )
        wait_for_command(interactive, resolve_display)
        run(
            sys.executable,
            "-m",
            "assert_ai.integrations.sandbox.cli",
            "resolve",
            str(SETUP),
            "send_message",
            "--args",
            json.dumps({"recipient": "555-000-9999", "channel": "sms"}),
        )

        sandbox_display = (
            "python examples/sandbox_action_mediation/run_stock_scenario.py "
            "--check-baseline"
        )
        wait_for_command(interactive, sandbox_display)
        run(sys.executable, str(RUNNER), "--check-baseline")

        cleanup_display = (
            "docker container ls -a --filter name=assert-sandbox-  &&  "
            "docker network ls --filter name=assert-sandbox-net-"
        )
        wait_for_command(interactive, cleanup_display)
        containers = docker_resource_count("container", "assert-sandbox-")
        networks = docker_resource_count("network", "assert-sandbox-net-")
        print(f"Remaining ASSERT containers: {containers}")
        print(f"Remaining ASSERT networks:   {networks}")
        if containers or networks:
            raise RuntimeError("sandbox cleanup left Docker resources behind")

        print("\nDEMO COMPLETE")
        return 0
    except KeyboardInterrupt:
        print("\n\nDemo stopped by presenter.", file=sys.stderr)
        return 130
    except (subprocess.CalledProcessError, RuntimeError) as exc:
        print(f"\nDEMO FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
