# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest

from assert_ai.config import load_runtime_context
from assert_ai.core.artifact_cache import (
    activate_artifact_plan,
    finalize_artifact_plan,
    prepare_artifact_plan,
)
from assert_ai.core.run_plan import resolve_forced_stages
from assert_ai.core.workspace import WorkspaceService
from assert_ai.services.configs import ConfigService
from assert_ai.services.errors import ServiceError, ServiceErrorCode
from assert_ai.services.run_planning import (
    EvaluationOverrides,
    ModelOverrides,
    PreflightPolicy,
    RunPlanningService,
    StageAction,
)
from assert_ai.stages import STAGES


def _document(*, model_target: bool = False) -> dict:
    target = (
        {"model": {"name": "openai/gpt-test"}}
        if model_target
        else {"callable": "agent:run"}
    )
    return {
        "suite": "demo-suite",
        "behavior": {
            "name": "safe_help",
            "description": "The agent should provide safe help.",
        },
        "default_model": {"name": "openai/gpt-test"},
        "pipeline": {
            "systematize": {},
            "test_set": {
                "prompt": {"sample_size": 2},
            },
            "inference": {
                "target": target,
                "concurrency": 2,
            },
            "judge": {},
        },
    }


def _services(
    root: Path,
    *,
    policy: PreflightPolicy | None = None,
) -> tuple[ConfigService, RunPlanningService]:
    workspace = WorkspaceService.create(root)
    configs = ConfigService(workspace)
    return (
        configs,
        RunPlanningService(
            workspace,
            configs,
            policy=policy or PreflightPolicy(),
        ),
    )


def test_preflight_is_pure_and_matches_force_cascade() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        configs, planning = _services(
            root,
            policy=PreflightPolicy(
                allowed_model_patterns=("openai/*",),
            ),
        )
        configs.save_config("demo.yaml", document=_document())
        before = {
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
        }

        with patch.dict(
            os.environ,
            {"OPENAI_API_KEY": "configured-for-test"},
            clear=False,
        ):
            result = planning.preflight(
                "demo.yaml",
                overrides=EvaluationOverrides(
                    run="candidate-a",
                    force_stages=("test_set",),
                    strict=True,
                    concurrency=3,
                    prompt_sample_size=4,
                ),
            )

        after = {
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
        }
        stages = {stage.name: stage for stage in result.stages}
        assert result.ready is True
        assert result.run_id == "candidate-a"
        assert result.strict is True
        assert result.concurrency == 3
        assert result.sample_sizes["prompt"] == 4
        assert result.target is not None
        assert result.target.kind == "callable"
        assert result.target.probe_required is True
        assert stages["systematize"].forced is False
        assert stages["test_set"].forced is True
        assert stages["inference"].forced is True
        assert stages["judge"].forced is True
        assert all(
            stage.action is StageAction.RUN
            for stage in result.stages
        )
        assert result.managed_outputs["suite_root"] == (
            "artifacts/results/demo-suite"
        )
        assert result.managed_outputs["run_root"].endswith(
            "/candidate-a"
        )
        assert before == after


def test_preflight_reports_policy_and_credential_blockers() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        configs, planning = _services(
            root,
            policy=PreflightPolicy(
                max_concurrency=2,
                max_prompt_sample_size=5,
                allowed_model_patterns=("azure/*",),
            ),
        )
        document = _document(model_target=True)
        document["pipeline"]["inference"]["concurrency"] = 8
        document["pipeline"]["test_set"]["prompt"]["sample_size"] = 10
        configs.save_config("demo.yaml", document=document)

        with patch.dict(
            os.environ,
            {"OPENAI_API_KEY": ""},
            clear=False,
        ):
            result = planning.preflight("demo.yaml")

        codes = {issue.code for issue in result.blocking_issues}
        assert result.ready is False
        assert "CONCURRENCY_LIMIT_EXCEEDED" in codes
        assert "SAMPLE_SIZE_LIMIT_EXCEEDED" in codes
        assert "MODEL_NOT_ALLOWED" in codes
        assert "CREDENTIAL_CONFIGURATION_MISSING" in codes
        assert result.credentials[0].variables == {
            "OPENAI_API_KEY": False,
        }


def test_preflight_returns_structural_validation_without_side_effects() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        configs, planning = _services(root)
        configs.workspace.configs_root.mkdir(parents=True)
        bad_path = configs.workspace.configs_root / "bad.yaml"
        bad_path.write_text(
            "pipeline:\n  inference:\n    unknown: true\n",
            encoding="utf-8",
        )

        result = planning.preflight("bad.yaml")

        assert result.ready is False
        assert result.stages == ()
        assert result.blocking_issues[0].code == "UNKNOWN_FIELD"
        assert not configs.workspace.artifacts_root.exists()


def test_model_override_cannot_replace_callable_target() -> None:
    with TemporaryDirectory() as tmp:
        configs, planning = _services(Path(tmp))
        configs.save_config("demo.yaml", document=_document())

        with pytest.raises(ServiceError) as invalid:
            planning.preflight(
                "demo.yaml",
                overrides=EvaluationOverrides(
                    models=ModelOverrides(
                        target_model="openai/replacement",
                    )
                ),
            )

        assert invalid.value.code == ServiceErrorCode.INVALID_ARGUMENT


def test_sandbox_preflight_is_static_and_not_a_model_target() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        configs, planning = _services(root)
        configs.workspace.configs_root.mkdir(parents=True)
        (configs.workspace.configs_root / "policy.yaml").write_text(
            "interactions: []\ndefault: {mode: block}\n",
            encoding="utf-8",
        )
        (configs.workspace.configs_root / "setup.yaml").write_text(
            "version: 1\n"
            "target: {kind: endpoint, url: 'https://agent.example.test/chat'}\n"
            "policy: ./policy.yaml\n",
            encoding="utf-8",
        )
        document = _document()
        document["pipeline"]["inference"]["target"] = {
            "sandbox": "./setup.yaml",
        }
        configs.save_config("demo.yaml", document=document)

        result = planning.preflight("demo.yaml")

        assert result.target is not None
        assert result.target.kind == "sandbox"
        assert result.target.identifier == "evals/setup.yaml"
        assert result.target.probe_required is False
        assert not any(model.role == "target" for model in result.models)

        restricted = RunPlanningService(
            planning.workspace,
            configs,
            policy=PreflightPolicy(
                allowed_endpoint_hosts=("api.example.test",),
            ),
        ).preflight("demo.yaml")
        assert "ENDPOINT_NOT_ALLOWED" in {
            issue.code for issue in restricted.blocking_issues
        }

        with pytest.raises(ServiceError) as invalid:
            planning.preflight(
                "demo.yaml",
                overrides=EvaluationOverrides(
                    models=ModelOverrides(
                        target_model="openai/replacement",
                    )
                ),
            )
        assert invalid.value.code == ServiceErrorCode.INVALID_ARGUMENT


def test_stratify_model_planning_matches_runtime_fallback_order() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        configs, planning = _services(
            root,
            policy=PreflightPolicy(
                allowed_model_patterns=("azure/*",),
            ),
        )
        document = _document()
        document["pipeline"]["systematize"]["enabled"] = False
        document["pipeline"]["inference"]["enabled"] = False
        document["pipeline"]["judge"]["enabled"] = False
        document["pipeline"]["test_set"]["model"] = {
            "name": "azure/test-set",
        }
        document["pipeline"]["test_set"]["stratify"] = {}
        configs.save_config("demo.yaml", document=document)

        with patch.dict(
            os.environ,
            {
                "OPENAI_API_KEY": "configured-for-test",
                "AZURE_API_BASE": "https://example.openai.azure.com",
                "AZURE_API_KEY": "configured-for-test",
            },
            clear=False,
        ):
            result = planning.preflight("demo.yaml")

        models = {model.role: model.model for model in result.models}
        assert models["test_set_prompt"] == "azure/test-set"
        assert models["test_set_stratify"] == "openai/gpt-test"
        model_issues = [
            issue
            for issue in result.blocking_issues
            if issue.code == "MODEL_NOT_ALLOWED"
        ]
        assert len(model_issues) == 1
        assert model_issues[0].path == "/pipeline/test_set/stratify/model"


def test_preflight_reuses_cache_without_writing_workspace() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        configs, planning = _services(root)
        configs.save_config("demo.yaml", document=_document())
        record = configs.get_config("demo.yaml")
        config_path = configs.workspace.path_policy.resolve_config_path(
            record.config_ref,
            must_exist=True,
            reject_links=True,
        )
        ctx = load_runtime_context(
            deepcopy(record.document),
            config_path,
            stage_modules=STAGES,
            path_policy=configs.workspace.path_policy,
        )
        raw_cfg = dict(
            next(
                raw
                for name, raw in ctx["stages"]
                if name == "systematize"
            )
        )
        plan = prepare_artifact_plan(
            ctx=ctx,
            stage_name="systematize",
            raw_cfg=raw_cfg,
            forced=False,
        )
        activate_artifact_plan(ctx, plan)
        plan.output_paths["taxonomy"].parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        plan.output_paths["taxonomy"].write_text(
            '{"behavior_categories":[]}',
            encoding="utf-8",
        )
        plan.output_paths["systematization"].write_text(
            "{}",
            encoding="utf-8",
        )
        finalize_artifact_plan(ctx, plan)
        before = {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*")
            if path.is_file()
        }

        with patch.dict(
            os.environ,
            {"OPENAI_API_KEY": "configured-for-test"},
            clear=False,
        ):
            result = planning.preflight("demo.yaml")

        after = {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*")
            if path.is_file()
        }
        stages = {stage.name: stage for stage in result.stages}
        assert stages["systematize"].action is StageAction.REUSE
        assert stages["systematize"].artifact_version == plan.version
        assert before == after


def test_resolve_forced_stages_rejects_missing_and_cascades() -> None:
    assert resolve_forced_stages(
        ("systematize", "test_set", "inference", "judge"),
        ("test_set",),
    ) == ("test_set", "inference", "judge")

    with pytest.raises(ValueError, match="missing"):
        resolve_forced_stages(("inference",), ("missing",))
