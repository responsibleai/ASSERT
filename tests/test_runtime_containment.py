# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import asyncio
import importlib
import json
import sys
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, patch

import pytest
import yaml

from assert_ai import runner
from assert_ai.config import load_runtime_context, resolve_stage_paths
from assert_ai.core import artifact_cache, tool_backend
from assert_ai.core.config_model import (
    EvaluationConfig,
    InferenceConfig,
    JudgeConfig,
    TargetConfig,
    TraceConfig,
)
from assert_ai.core.model_client import GenerateOptions
from assert_ai.core.runtime_path_policy import RuntimePathError, RuntimePathErrorCode
from assert_ai.core.tools import resolve_toolset_path
from assert_ai.core.workspace import WorkspaceService
from assert_ai.integrations.sandbox import load_setup
from assert_ai.stages import STAGES, inference, judge


@pytest.fixture
def workspace(tmp_path: Path) -> Iterator[tuple[WorkspaceService, Path]]:
    root = tmp_path / "workspace"
    (root / "evals").mkdir(parents=True)
    config = root / "evals" / "eval_config.yaml"
    config.write_text(yaml.safe_dump(_config()), encoding="utf-8")
    existing_finders = set(tool_backend._WORKSPACE_FINDERS)
    yield WorkspaceService.create(root), config
    for namespace in set(tool_backend._WORKSPACE_FINDERS) - existing_finders:
        finder = tool_backend._WORKSPACE_FINDERS.pop(namespace)
        sys.meta_path.remove(finder)
        for name in tuple(sys.modules):
            if name == namespace or name.startswith(f"{namespace}."):
                sys.modules.pop(name, None)


def _config() -> dict:
    return {
        "suite": "suite-a",
        "run": "run-a",
        "pipeline": {"inference": {"enabled": False}},
    }


def _context(workspace: WorkspaceService, config: Path, raw: dict | None = None) -> dict:
    return load_runtime_context(
        raw or _config(),
        config,
        stage_modules=STAGES,
        path_policy=workspace.path_policy,
    )


def test_runtime_roots_and_config_references_do_not_depend_on_cwd(
    workspace, tmp_path, monkeypatch
):
    managed, config = workspace
    monkeypatch.chdir(tmp_path)
    ctx = runner._load_context(
        config=config.name, path_policy=managed.path_policy
    )
    assert ctx["config_path"] == config
    assert ctx["artifacts_root"] == managed.artifacts_root
    assert ctx["results_dir"] == managed.results_root
    assert ctx["path_policy"] is managed.path_policy
    assert not managed.artifacts_root.exists()


@pytest.mark.parametrize("key,value", [
    ("artifacts_root", "other-artifacts"),
    ("results_dir", "other-results"),
])
def test_config_cannot_override_managed_roots(workspace, key, value):
    managed, config = workspace
    with pytest.raises(RuntimePathError) as error:
        _context(managed, config, {**_config(), key: value})
    assert error.value.code is RuntimePathErrorCode.MANAGED_ROOT_OVERRIDE


@pytest.mark.parametrize("stage,key", [
    ("systematize", "file_path"),
    ("test_set", "taxonomy_path"),
    ("inference", "test_set_path"),
    ("judge", "inference_set_path"),
    ("judge", "taxonomy_path"),
])
def test_context_validates_even_disabled_stage_inputs(workspace, stage, key):
    managed, config = workspace
    raw = {**_config(), "pipeline": {stage: {"enabled": False, key: "../outside.json"}}}
    with pytest.raises(RuntimePathError) as error:
        _context(managed, config, raw)
    assert error.value.code is RuntimePathErrorCode.OUTSIDE_INPUT_ROOT
    assert error.value.field_name == f"pipeline.{stage}.{key}"


@pytest.mark.parametrize("stage,key", [
    ("systematize", "save_dir"),
    ("test_set", "save_path"),
    ("inference", "save_dir"),
    ("judge", "save_dir"),
])
def test_stage_outputs_cannot_redirect_to_other_suites(workspace, stage, key):
    managed, config = workspace
    output = managed.results_root / "suite-b" / "output"
    raw = {**_config(), "pipeline": {stage: {"enabled": False, key: str(output)}}}
    with pytest.raises(RuntimePathError) as error:
        _context(managed, config, raw)
    assert error.value.code is RuntimePathErrorCode.OUTSIDE_EXPECTED_ROOT


def test_stage_path_resolution_rejects_other_runs_in_the_same_suite(workspace):
    managed, config = workspace
    suite_root = managed.results_root / "suite-a"
    with pytest.raises(RuntimePathError) as error:
        resolve_stage_paths(
            {"save_dir": str(suite_root / "run-b")},
            cfg_path=config,
            artifacts_root=managed.artifacts_root,
            path_policy=managed.path_policy,
            managed_output_root=suite_root / "run-a",
        )
    assert error.value.code is RuntimePathErrorCode.OUTSIDE_EXPECTED_ROOT


def test_runner_rejects_external_config_before_reading_it(workspace, tmp_path, caplog):
    managed, _ = workspace
    outside = tmp_path / "outside.yaml"
    outside.write_text("not: an eval\n", encoding="utf-8")
    with patch.object(runner, "load_config", side_effect=AssertionError("must not read")):
        assert runner.run_pipeline(config=str(outside), path_policy=managed.path_policy) == 1
        with pytest.raises(RuntimePathError):
            runner.estimate_pipeline_usage(config=str(outside), path_policy=managed.path_policy)
    assert "[config error]" in caplog.text
    assert not managed.artifacts_root.exists()


@pytest.mark.parametrize("relative", [
    Path("suite.json"),
    Path("run-a") / "config.yaml",
    Path("run-a") / "manifest.json",
    Path("run-a") / "metrics.json",
])
def test_runner_rejects_existing_linked_metadata(
    workspace, tmp_path, relative, symlink_or_skip
):
    managed, config = workspace
    protected = tmp_path / "protected.json"
    protected.write_text('{"keep": true}\n', encoding="utf-8")
    link = managed.results_root / "suite-a" / relative
    link.parent.mkdir(parents=True, exist_ok=True)
    symlink_or_skip(link, protected)
    assert runner.run_pipeline(config=str(config), path_policy=managed.path_policy) == 1
    assert protected.read_text(encoding="utf-8") == '{"keep": true}\n'


def test_cache_preview_and_read_only_activation_do_not_write(workspace):
    managed, config = workspace
    ctx = _context(managed, config)
    preview = artifact_cache.preview_artifact_plan(
        ctx=ctx, stage_name="systematize", raw_cfg={}, forced=False
    )
    assert not preview.reused
    assert not managed.artifacts_root.exists()

    plan = artifact_cache.prepare_artifact_plan(
        ctx=ctx, stage_name="systematize", raw_cfg={}, forced=False
    )
    for path in plan.output_paths.values():
        path.write_text("{}\n", encoding="utf-8")
    artifact_cache.finalize_artifact_plan(ctx, plan)
    suite = Path(ctx["suite_root"])
    (suite / "taxonomy.json").unlink()
    latest_path = suite / "latest.json"
    latest = json.loads(latest_path.read_text(encoding="utf-8"))
    latest["artifacts"]["systematize"]["artifact_dir"] = "missing"
    latest["artifacts"]["systematize"]["metadata_path"] = "missing/artifact.json"
    latest_path.write_text(json.dumps(latest), encoding="utf-8")
    before = {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in managed.artifacts_root.rglob("*") if path.is_file()
    }
    other_ctx = _context(managed, config)
    artifact_cache.activate_latest_artifacts(other_ctx, read_only=True)
    assert other_ctx["artifact_versions"]["systematize"]["version"] == plan.version
    assert artifact_cache.preview_artifact_plan(
        ctx=other_ctx, stage_name="systematize", raw_cfg={}, forced=False
    ).reused
    runner.estimate_pipeline_usage(config=config.name, path_policy=managed.path_policy)
    assert not (suite / "taxonomy.json").exists()
    after = {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in managed.artifacts_root.rglob("*") if path.is_file()
    }
    assert after == before


def test_cache_rejects_version_traversal(workspace):
    managed, config = workspace
    ctx = _context(managed, config)
    suite = Path(ctx["suite_root"])
    suite.mkdir(parents=True)
    (suite / "latest.json").write_text(json.dumps({
        "artifacts": {"systematize": {"version": "../../other-stage"}}
    }), encoding="utf-8")
    with pytest.raises(RuntimePathError):
        artifact_cache.activate_latest_artifacts(ctx, read_only=True)


def test_cache_rejects_external_writes_and_cleanup(workspace, tmp_path, caplog):
    managed, config = workspace
    ctx = _context(managed, config)
    plan = artifact_cache.prepare_artifact_plan(
        ctx=ctx, stage_name="systematize", raw_cfg={}, forced=False
    )
    outside = tmp_path / "outside-cache"
    outside.mkdir()
    protected = outside / "taxonomy.json"
    protected.write_text('{"keep": true}\n', encoding="utf-8")
    invalid = replace(plan, artifact_dir=outside, output_paths={"taxonomy": protected})
    with pytest.raises(RuntimePathError):
        artifact_cache.finalize_artifact_plan(ctx, invalid)
    with pytest.raises(RuntimePathError):
        artifact_cache.refresh_compatibility_files(ctx, "systematize", invalid.output_paths)
    artifact_cache.discard_artifact_plan(ctx, invalid)
    assert "refusing to clean up" in caplog.text
    assert protected.read_text(encoding="utf-8") == '{"keep": true}\n'


def test_cache_rejects_linked_version_directories(workspace, tmp_path, symlink_or_skip):
    managed, config = workspace
    ctx = _context(managed, config)
    outside = tmp_path / "outside-cache"
    outside.mkdir()
    link = Path(ctx["suite_root"]) / "artifacts" / "systematize" / "v0001"
    link.parent.mkdir(parents=True, exist_ok=True)
    symlink_or_skip(link, outside)
    with pytest.raises(RuntimePathError):
        artifact_cache.preview_artifact_plan(
            ctx=ctx, stage_name="systematize", raw_cfg={}, forced=False
        )
    assert not list(outside.iterdir())


def test_workspace_imports_ignore_cwd_and_preloaded_external_modules(
    workspace, tmp_path, monkeypatch
):
    managed, config = workspace
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "external_agent.py").write_text("VALUE = 'external'\n", encoding="utf-8")
    monkeypatch.chdir(outside)
    monkeypatch.syspath_prepend(str(outside))
    external = ModuleType("external_agent")
    external.VALUE = "preloaded"
    monkeypatch.setitem(sys.modules, "external_agent", external)
    with pytest.raises(ValueError, match="inside the configured workspace"):
        tool_backend.import_callable_module(
            "external_agent", config_path=config, path_policy=managed.path_policy
        )
    with pytest.raises(RuntimePathError):
        tool_backend.load_tool_module(
            str(outside / "external_agent.py"),
            config_path=config,
            path_policy=managed.path_policy,
        )


@pytest.mark.parametrize("import_statement,expression", [
    ("import helpers", "helpers.VALUE"),
    ("from .helpers import VALUE", "VALUE"),
    ("from importlib import import_module", "import_module('helpers').VALUE"),
    ("import importlib", "importlib.import_module('helpers').VALUE"),
    ("import importlib.util", "importlib.import_module('helpers').VALUE"),
    ("import json; import helpers", "json.loads(json.dumps(helpers.VALUE))"),
])
def test_workspace_imports_isolate_lazy_helpers_between_configs(
    workspace, monkeypatch, import_statement, expression
):
    managed, _ = workspace
    configs = []
    for label in ("first", "second"):
        root = managed.configs_root / label
        root.mkdir()
        config = root / "eval.yaml"
        config.write_text("", encoding="utf-8")
        (root / "helpers.py").write_text(f"VALUE = {label!r}\n", encoding="utf-8")
        (root / "agent.py").write_text(
            f"def value():\n    {import_statement}\n    return {expression}\n",
            encoding="utf-8",
        )
        configs.append(config)
    external = ModuleType("helpers")
    external.VALUE = "external"
    monkeypatch.setitem(sys.modules, "helpers", external)
    original_path = list(sys.path)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                tool_backend.import_callable_module,
                "agent",
                config_path=config,
                path_policy=managed.path_policy,
            )
            for config in configs
        ]
        modules = [future.result() for future in futures]
    assert [module.value() for module in modules] == ["first", "second"]
    assert modules[0].__name__ != modules[1].__name__
    assert sys.path == original_path


def test_workspace_imports_preserve_dependency_errors(workspace, monkeypatch):
    managed, config = workspace
    (config.parent / "agent.py").write_text(
        "from importlib import missing_feature\n", encoding="utf-8"
    )
    original_import = importlib.import_module

    def import_with_missing_dependency(name, package=None):
        if name == "importlib.missing_feature":
            raise ModuleNotFoundError("missing dependency", name="missing_dependency")
        return original_import(name, package)

    monkeypatch.setattr(importlib, "import_module", import_with_missing_dependency)
    with pytest.raises(ModuleNotFoundError) as error:
        tool_backend.import_callable_module(
            "agent", config_path=config, path_policy=managed.path_policy
        )
    assert error.value.name == "missing_dependency"


def test_import_namespace_includes_workspace_boundary(workspace):
    managed, _ = workspace
    inner_root = managed.configs_root
    shared = inner_root / "shared"
    shared.mkdir()
    config = shared / "eval.yaml"
    config.write_text("", encoding="utf-8")
    (shared / "agent.py").write_text(
        "import helpers\nVALUE = helpers.VALUE\n", encoding="utf-8"
    )
    (managed.root / "helpers.py").write_text("VALUE = 'outer'\n", encoding="utf-8")
    (inner_root / "helpers.py").write_text("VALUE = 'inner'\n", encoding="utf-8")
    inner_policy = replace(
        managed.path_policy,
        workspace_root=inner_root,
        config_root=shared,
        artifacts_root=inner_root / "artifacts",
        results_root=inner_root / "artifacts" / "results",
    )
    outer = tool_backend.import_callable_module(
        "agent", config_path=config, path_policy=managed.path_policy
    )
    inner = tool_backend.import_callable_module(
        "agent", config_path=config, path_policy=inner_policy
    )
    assert outer.VALUE == "outer"
    assert inner.VALUE == "inner"


@pytest.mark.parametrize("imports", [
    "from .helper import VALUE\nfrom local_tools.helper import VALUE as ABSOLUTE\n",
    "from local_tools import *\nVALUE = ABSOLUTE = helper.VALUE\n",
    "import local_tools.helper as helper\nVALUE = ABSOLUTE = helper.VALUE\n",
])
def test_direct_tool_modules_support_package_imports(workspace, imports):
    managed, config = workspace
    package = config.parent / "local_tools"
    package.mkdir()
    (package / "__init__.py").write_text("__all__ = ['helper']\n", encoding="utf-8")
    (package / "helper.py").write_text("VALUE = 'local'\n", encoding="utf-8")
    source = package / "tools.py"
    source.write_text(imports, encoding="utf-8")
    module = tool_backend.load_tool_module(
        str(source), config_path=config, path_policy=managed.path_policy
    )
    assert module.VALUE == module.ABSOLUTE == "local"


@pytest.mark.parametrize("target,session_class", [
    (TargetConfig(callable="agent:run"), "assert_ai.stages.inference.CallableSession"),
    (
        TargetConfig(callable="agent:run", trace=TraceConfig()),
        "assert_ai.core.otel_session.OTelTracedSession",
    ),
    (TargetConfig(connector="connector"), "assert_ai.stages.inference.ExternalSession"),
    (
        TargetConfig(sandbox="sandbox.yaml"),
        "assert_ai.integrations.sandbox.session.SandboxedEndpointSession",
    ),
])
def test_target_factories_forward_the_path_policy(workspace, target, session_class):
    managed, config = workspace
    with patch(session_class) as constructor:
        result = inference._build_target_session(
            target=target,
            test_case_payload={},
            inference=InferenceConfig(),
            max_tokens=100,
            config_path=config,
            path_policy=managed.path_policy,
        )
    assert result is constructor.return_value
    assert constructor.call_args.kwargs["path_policy"] is managed.path_policy
    assert constructor.call_args.kwargs["config_path"] == config


def test_toolset_is_revalidated_before_runtime_loading(workspace, tmp_path):
    managed, config = workspace
    outside = tmp_path / "tools.json"
    outside.write_text("[]\n", encoding="utf-8")
    with pytest.raises(RuntimePathError):
        inference._build_hosted_session(
            model="mock/model",
            tools_config={
                "_config_path": str(config),
                "toolset": str(outside),
                "simulator": "mock/simulator",
            },
            scenario={},
            generate_options=GenerateOptions(),
            max_tool_calls=1,
            synthetic_prompt_template="{scenario}",
            path_policy=managed.path_policy,
        )


def test_shared_toolset_resolver_has_no_cwd_fallback_with_policy(
    workspace, tmp_path, monkeypatch
):
    managed, config = workspace
    (tmp_path / "tools.yaml").write_text("[]\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert resolve_toolset_path("tools.yaml", config_path=config) == tmp_path / "tools.yaml"
    with pytest.raises(RuntimePathError) as error:
        resolve_toolset_path("tools.yaml", config_path=config, path_policy=managed.path_policy)
    assert error.value.code is RuntimePathErrorCode.PATH_NOT_FOUND


@pytest.mark.parametrize("reference", ["policy", "mocks", "cassettes", "nested-cassettes"])
def test_sandbox_setup_confines_all_referenced_inputs(workspace, tmp_path, reference):
    managed, config = workspace
    policy_path = config.parent / "policy.yaml"
    policy_path.write_text("default:\n  mode: block\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    external = outside / "reference.yaml"
    external.write_text("mocks: []\n", encoding="utf-8")
    setup = {
        "target": {"kind": "endpoint", "url": "http://127.0.0.1:8080/chat"},
        "policy": "policy.yaml",
    }
    if reference == "nested-cassettes":
        mocks_path = config.parent / "mocks.yaml"
        mocks_path.write_text(
            yaml.safe_dump({"mocks": [], "cassette_dir": str(outside)}), encoding="utf-8"
        )
        setup["mocks"] = "mocks.yaml"
    else:
        setup[reference] = str(outside if reference == "cassettes" else external)
    setup_path = config.parent / "sandbox.yaml"
    setup_path.write_text(yaml.safe_dump(setup), encoding="utf-8")
    with pytest.raises(RuntimePathError) as error:
        load_setup(setup_path, path_policy=managed.path_policy)
    assert error.value.code is RuntimePathErrorCode.OUTSIDE_INPUT_ROOT


def test_sandbox_fingerprint_resolves_relative_to_policy_without_cwd(workspace, monkeypatch, tmp_path):
    managed, config = workspace
    (config.parent / "policy.yaml").write_text("default:\n  mode: block\n", encoding="utf-8")
    (config.parent / "sandbox.yaml").write_text(yaml.safe_dump({
        "target": {"kind": "endpoint", "url": "http://127.0.0.1:8080/chat"},
        "policy": "policy.yaml",
    }), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    fingerprint = inference._inference_config_fingerprint(
        TargetConfig(sandbox="sandbox.yaml"), None, 100,
        path_policy=managed.path_policy,
    )
    assert len(fingerprint) == 16


def test_low_level_stage_inputs_and_outputs_are_contained(workspace, tmp_path):
    managed, config = workspace
    external = tmp_path / "external.jsonl"
    external.write_text("{}\n", encoding="utf-8")
    run_root = managed.results_root / "suite-a" / "run-a"
    with pytest.raises(RuntimePathError):
        asyncio.run(inference.run_inference(
            test_set_path=str(external),
            target=TargetConfig(callable="agent:run"),
            config_path=config,
            path_policy=managed.path_policy,
            managed_output_root=run_root,
            rewrite_test_set_path=False,
        ))
    with pytest.raises(RuntimePathError):
        asyncio.run(judge.run_judge(
            inference_set_path=str(external),
            evaluation=EvaluationConfig(judge=JudgeConfig(model="mock/model")),
            config_path=config,
            path_policy=managed.path_policy,
            managed_output_root=run_root,
        ))
    inside = config.parent / "input.jsonl"
    inside.write_text("{}\n", encoding="utf-8")
    with pytest.raises(RuntimePathError):
        asyncio.run(inference.run_inference(
            test_set_path=str(inside),
            save_dir=str(tmp_path / "outside-output"),
            target=TargetConfig(callable="agent:run"),
            config_path=config,
            path_policy=managed.path_policy,
            managed_output_root=run_root,
            rewrite_test_set_path=False,
        ))
    assert not managed.artifacts_root.exists()
    assert not (tmp_path / "outside-output").exists()


@pytest.mark.parametrize("stage,workflow,output_key", [
    (inference, "run_inference", "inference_set_path"),
    (judge, "run_judge", "scores_path"),
])
def test_no_policy_stage_wrappers_still_accept_explicit_paths_without_run_root(
    workspace, stage, workflow, output_key
):
    managed, config = workspace
    ctx = {
        "config_path": config,
        "artifacts_root": managed.artifacts_root,
        "suite_root": None,
        "run_root": None,
        "run_id": None,
        "target": TargetConfig(callable="agent:run"),
        "evaluation": EvaluationConfig(judge=JudgeConfig(model="mock/model")),
    }
    output = str(managed.root / "custom-output")
    raw = {
        "test_set_path": str(managed.root / "input.jsonl"),
        "inference_set_path": str(managed.root / "inference.jsonl"),
        "taxonomy_path": str(managed.root / "taxonomy.json"),
        "save_dir": output,
    }
    with patch.object(stage, workflow, new_callable=AsyncMock) as execute:
        execute.return_value = {output_key: output}
        result = asyncio.run(stage.run(ctx, raw))
    assert result[output_key] == output
    assert execute.call_args.kwargs["path_policy"] is None


def test_contained_run_preserves_read_only_input_and_estimator_resume(workspace, tmp_path):
    managed, config = workspace
    input_root = tmp_path / "approved-inputs"
    input_root.mkdir()
    source = input_root / "cases.jsonl"
    original = (
        b'{ "type": "prompt", "test_case_id": "test_case_000001", '
        b'"seed": {"description": "Hello"} }\n'
    )
    source.write_bytes(original)
    policy = replace(managed.path_policy, additional_read_roots=(input_root,))
    (managed.root / "agent.py").write_text(
        "CALLS = 0\n"
        "def run(message):\n"
        "    global CALLS\n"
        "    CALLS += 1\n"
        "    return 'Echo: ' + message\n",
        encoding="utf-8",
    )
    config.write_text(yaml.safe_dump({
        **_config(),
        "pipeline": {"inference": {
            "target": {"callable": "agent:run"},
            "test_set_path": str(source),
        }},
    }), encoding="utf-8")
    runner.estimate_pipeline_usage(config=config.name, path_policy=policy)
    assert not managed.artifacts_root.exists()
    assert runner.run_pipeline(config=config.name, path_policy=policy) == 0
    run_root = managed.results_root / "suite-a" / "run-a"
    rows = (run_root / "inference_set.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1
    assert json.loads(rows[0])["test_case_id"] == "test_case_000001"
    assert source.read_bytes() == original
    with patch.object(
        inference, "_inference_config_fingerprint", wraps=inference._inference_config_fingerprint
    ) as fingerprint:
        runner.estimate_pipeline_usage(config=config.name, path_policy=policy)
    assert fingerprint.call_count == 1
    assert fingerprint.call_args.kwargs["test_set_content"] is None
    assert runner.run_pipeline(config=config.name, path_policy=policy) == 0
    module = tool_backend.import_callable_module(
        "agent", config_path=config, path_policy=policy
    )
    assert module.CALLS == 1
    assert source.read_bytes() == original
    assert json.loads((run_root / "manifest.json").read_text(encoding="utf-8"))["status"] == "completed"
