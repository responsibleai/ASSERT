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


def _cache_systematization(configs: ConfigService, config_ref: str = "demo.yaml") -> str:
    record = configs.get_config(config_ref)
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
    raw_cfg = dict(next(raw for name, raw in ctx["stages"] if name == "systematize"))
    plan = prepare_artifact_plan(
        ctx=ctx,
        stage_name="systematize",
        raw_cfg=raw_cfg,
        forced=False,
    )
    activate_artifact_plan(ctx, plan)
    plan.output_paths["taxonomy"].parent.mkdir(parents=True, exist_ok=True)
    plan.output_paths["taxonomy"].write_text(
        '{"behavior_categories":[]}', encoding="utf-8"
    )
    plan.output_paths["systematization"].write_text("{}", encoding="utf-8")
    finalize_artifact_plan(ctx, plan)
    assert plan.version is not None
    return plan.version


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


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize(
    "target",
    [
        {"callable": "agent:run"},
        {"endpoint": "https://agent.example.test/chat"},
        {"connector": "agent_connector"},
        {"sandbox": "./setup.yaml"},
    ],
    ids=["callable", "endpoint", "connector", "sandbox"],
)
def test_preflight_keeps_active_opaque_target_calls_unknown(
    tmp_path: Path, target: dict[str, str], enabled: bool
) -> None:
    configs, planning = _services(tmp_path)
    configs.workspace.configs_root.mkdir(parents=True)
    if "sandbox" in target:
        (configs.workspace.configs_root / "policy.yaml").write_text(
            "interactions: []\ndefault: {mode: block}\n", encoding="utf-8"
        )
        (configs.workspace.configs_root / "setup.yaml").write_text(
            "version: 1\n"
            "target: {kind: endpoint, url: 'https://agent.example.test/chat'}\n"
            "policy: ./policy.yaml\n",
            encoding="utf-8",
        )
    (configs.workspace.configs_root / "cases.jsonl").write_text(
        '{"type":"prompt","test_case_id":"one","seed":{"prompt":"hello"}}\n',
        encoding="utf-8",
    )
    configs.save_config(
        "opaque.yaml",
        document={
            "suite": "opaque",
            "pipeline": {
                "inference": {
                    "enabled": enabled,
                    "target": dict(target),
                    "test_set_path": "cases.jsonl",
                }
            },
        },
    )

    result = planning.preflight("opaque.yaml")

    assert result.ready is True
    assert result.models == ()
    assert result.estimated_model_calls.minimum == 0
    assert result.estimated_model_calls.maximum == (None if enabled else 0)
    if enabled:
        assert "target cannot be determined statically" in result.estimated_model_calls.basis


@pytest.mark.parametrize("allowed", [True, False])
@pytest.mark.parametrize("credentials_present", [True, False])
def test_preflight_applies_model_policy_and_credentials_to_tool_simulators(
    tmp_path: Path, allowed: bool, credentials_present: bool
) -> None:
    patterns = ("openai/*", "anthropic/*") if allowed else ("openai/*",)
    configs, planning = _services(
        tmp_path, policy=PreflightPolicy(allowed_model_patterns=patterns)
    )
    document = _document(model_target=True)
    document["pipeline"]["test_set"]["tool_source"] = "per_test_case"
    document["pipeline"]["inference"]["target"]["tools"] = {
        "simulator": "anthropic/claude-test"
    }
    configs.save_config("simulated.yaml", document=document)

    with patch.dict(
        os.environ,
        {
            "OPENAI_API_KEY": "configured-for-test",
            "ANTHROPIC_API_KEY": "configured-for-test" if credentials_present else "",
        },
        clear=False,
    ):
        result = planning.preflight("simulated.yaml")

    simulator = next(model for model in result.models if model.role == "tool_simulator")
    assert simulator.stage == "inference"
    assert simulator.model == "anthropic/claude-test"
    assert simulator.provider == "anthropic"
    assert result.ready is (allowed and credentials_present)
    issues = {(issue.code, issue.path) for issue in result.blocking_issues}
    expected = set()
    if not allowed:
        expected.add(
            ("MODEL_NOT_ALLOWED", "/pipeline/inference/target/tools/simulator")
        )
    if not credentials_present:
        expected.add(("CREDENTIAL_CONFIGURATION_MISSING", ""))
    assert issues == expected


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
        version = _cache_systematization(configs)
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
        assert stages["systematize"].artifact_version == version
        assert result.consumed_artifacts["systematize"].version == version
        assert before == after

        with patch.dict(
            os.environ,
            {"OPENAI_API_KEY": "configured-for-test"},
            clear=False,
        ):
            forced = planning.preflight(
                "demo.yaml",
                overrides=EvaluationOverrides(
                    force_stages=("systematize",),
                ),
            )

        assert forced.stages[0].action is StageAction.RUN
        assert "systematize" not in forced.consumed_artifacts


@pytest.mark.parametrize("forced", [True, False])
@pytest.mark.parametrize("allowed", [True, False])
def test_cached_stages_require_credentials_only_when_forced(
    tmp_path: Path, forced: bool, allowed: bool
) -> None:
    configs, planning = _services(
        tmp_path,
        policy=PreflightPolicy(
            allowed_model_patterns=("openai/*",) if allowed else ("azure/*",)
        ),
    )
    document = _document()
    document["pipeline"] = {"systematize": document["pipeline"]["systematize"]}
    configs.save_config("demo.yaml", document=document)
    _cache_systematization(configs)
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}

    with patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=False):
        result = planning.preflight(
            "demo.yaml",
            overrides=EvaluationOverrides(
                force_stages=("systematize",) if forced else ()
            ),
        )

    assert result.ready is (allowed and not forced)
    assert result.models[0].model == "openai/gpt-test"
    assert result.stages[0].action is (StageAction.RUN if forced else StageAction.REUSE)
    assert bool(result.credentials) is forced
    assert result.estimated_model_calls.maximum == (None if forced else 0)
    codes = {issue.code for issue in result.blocking_issues}
    expected = set()
    if forced:
        expected.add("CREDENTIAL_CONFIGURATION_MISSING")
    if not allowed:
        expected.add("MODEL_NOT_ALLOWED")
    assert codes == expected
    assert before == {
        path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()
    }


def test_resolve_forced_stages_rejects_missing_and_cascades() -> None:
    assert resolve_forced_stages(
        ("systematize", "test_set", "inference", "judge"),
        ("test_set",),
    ) == ("test_set", "inference", "judge")

    with pytest.raises(ValueError, match="missing"):
        resolve_forced_stages(("inference",), ("missing",))
