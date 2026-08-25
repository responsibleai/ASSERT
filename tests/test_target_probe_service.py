# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from assert_ai.core.workspace import WorkspaceService
from assert_ai.services.configs import ConfigService
from assert_ai.services.errors import ServiceError, ServiceErrorCode
from assert_ai.services.target_probe import (
    TargetProbeService,
    _parse_worker_result,
)


def _callable_document(reference: str) -> dict:
    return {
        "suite": "probe-suite",
        "pipeline": {
            "inference": {
                "target": {"callable": reference},
                "test_set_path": "fixtures/test_set.jsonl",
            }
        },
    }


def _service(
    root: Path,
    *,
    timeout_s: float = 15.0,
) -> tuple[ConfigService, TargetProbeService]:
    workspace = WorkspaceService.create(root)
    configs = ConfigService(workspace)
    return configs, TargetProbeService(
        workspace,
        configs,
        timeout_s=timeout_s,
    )


def test_callable_probe_imports_only_in_worker() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "agent.py").write_text(
            "async def run(message, *, history=None):\n"
            "    return message\n",
            encoding="utf-8",
        )
        configs, probe = _service(root)
        configs.save_config(
            "demo.yaml",
            document=_callable_document("agent:run"),
        )

        result = probe.probe("demo.yaml")

        assert result.target_kind == "callable"
        assert result.isolated is True
        assert result.details["reference"] == "agent:run"
        assert result.details["is_async"] is True
        assert result.details["accepts_history"] is True
        assert result.details["parameters"] == [
            "message",
            "history",
        ]
        assert not (root / "__pycache__").exists()


def test_probe_failure_redacts_credentials_and_workspace_paths() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "broken.py").write_text(
            "raise RuntimeError("
            "f'AZURE_API_KEY=not-a-real-secret path={__file__}'"
            ")\n",
            encoding="utf-8",
        )
        configs, probe = _service(root)
        configs.save_config(
            "demo.yaml",
            document=_callable_document("broken:run"),
        )

        with pytest.raises(ServiceError) as failed:
            probe.probe("demo.yaml")

        assert failed.value.code == ServiceErrorCode.TARGET_IMPORT_FAILED
        assert "not-a-real-secret" not in str(failed.value)
        assert str(root) not in str(failed.value)
        assert "[REDACTED]" in str(failed.value)


def test_probe_timeout_terminates_worker() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "slow.py").write_text(
            "from pathlib import Path\n"
            "import subprocess\n"
            "import sys\n"
            "import time\n"
            "child = subprocess.Popen("
            "[sys.executable, '-c', 'import time; time.sleep(60)']"
            ")\n"
            "Path('child.pid').write_text(str(child.pid), encoding='utf-8')\n"
            "time.sleep(5)\n"
            "def run(message):\n"
            "    return message\n",
            encoding="utf-8",
        )
        configs, probe = _service(root, timeout_s=1.5)
        configs.save_config(
            "demo.yaml",
            document=_callable_document("slow:run"),
        )

        with pytest.raises(ServiceError) as timed_out:
            probe.probe("demo.yaml")

        assert timed_out.value.code == ServiceErrorCode.TARGET_IMPORT_FAILED
        assert timed_out.value.details == {"timed_out": True}
        assert "1.5 seconds" in str(timed_out.value)
        child_pid = int((root / "child.pid").read_text(encoding="utf-8"))
        import psutil

        assert not psutil.pid_exists(child_pid)


def test_model_probe_is_static_and_reports_model() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        configs, probe = _service(root)
        configs.save_config(
            "demo.yaml",
            document={
                "suite": "probe-suite",
                "pipeline": {
                    "inference": {
                        "target": {
                            "model": {"name": "openai/gpt-test"},
                        },
                        "test_set_path": "fixtures/test_set.jsonl",
                    }
                },
            },
        )

        result = probe.probe("demo.yaml")

        assert result.target_kind == "model"
        assert result.details == {
            "model": "openai/gpt-test",
            "trace_enabled": False,
        }


def test_worker_result_parser_requires_the_invocation_token() -> None:
    output = "\n".join(
        (
            'ASSERT_TARGET_PROBE_RESULT=expected={"ok":true}',
            'ASSERT_TARGET_PROBE_RESULT=forged={"ok":false}',
            'ASSERT_TARGET_PROBE_RESULT={"ok":false}',
        )
    )

    assert _parse_worker_result(
        output,
        result_token="expected",
    ) == {"ok": True}
    assert (
        _parse_worker_result(
            output,
            result_token="missing",
        )
        is None
    )
