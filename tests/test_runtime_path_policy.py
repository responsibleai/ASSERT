# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import importlib
import os
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from assert_ai.config import _resolve_path, load_runtime_context
from assert_ai.core.artifact_cache import (
    discard_artifact_plan,
    prepare_artifact_plan,
    refresh_compatibility_files,
    update_latest,
)
from assert_ai.core.model_client import GenerateOptions
from assert_ai.core.runtime_path_policy import (
    RuntimePathError,
    RuntimePathErrorCode,
    RuntimePathPolicy,
)
from assert_ai.core.security import validate_sys_path_addition
from assert_ai.core.tool_backend import import_callable_module, load_tool_module
from assert_ai.core.workspace import WorkspaceService
from assert_ai.stages import STAGES
from assert_ai.stages.inference import _build_hosted_session


def _workspace(tmp_path: Path) -> tuple[WorkspaceService, Path]:
    root = tmp_path / "workspace"
    configs = root / "evals"
    configs.mkdir(parents=True)
    config_path = configs / "eval_config.yaml"
    config_path.write_text("pipeline: {}\n", encoding="utf-8")
    return WorkspaceService.create(root), config_path


def _minimal_config() -> dict:
    return {
        "suite": "suite-a",
        "pipeline": {
            "inference": {
                "enabled": False,
            }
        },
    }


def _remove_workspace_imports(existing_modules: set[str]) -> None:
    for name in set(sys.modules).difference(existing_modules):
        if name.startswith("_assert_ai_workspace_"):
            sys.modules.pop(name, None)


def test_workspace_service_exposes_only_relative_references(tmp_path: Path) -> None:
    workspace, _ = _workspace(tmp_path)

    assert workspace.reference(workspace.root) == "."
    assert workspace.reference(workspace.configs_root) == "evals"
    assert workspace.reference(workspace.artifacts_root) == "artifacts"
    assert workspace.reference(workspace.results_root) == "artifacts/results"


def test_config_path_is_contained_under_config_root(tmp_path: Path) -> None:
    workspace, config_path = _workspace(tmp_path)

    assert workspace.path_policy.resolve_config_path("eval_config.yaml") == config_path
    assert workspace.path_policy.resolve_config_path("evals/eval_config.yaml") == config_path

    with pytest.raises(RuntimePathError) as exc:
        workspace.path_policy.resolve_config_path("../outside.yaml")

    assert exc.value.code is RuntimePathErrorCode.OUTSIDE_CONFIG_ROOT


def test_relative_input_cannot_escape_its_base_directory(tmp_path: Path) -> None:
    workspace, config_path = _workspace(tmp_path)
    shared = workspace.root / "shared.jsonl"
    shared.write_text("{}\n", encoding="utf-8")

    with pytest.raises(RuntimePathError) as exc:
        workspace.path_policy.resolve_input(
            "../shared.jsonl",
            base_dir=config_path.parent,
            field_name="pipeline.inference.test_set_path",
        )

    assert exc.value.code is RuntimePathErrorCode.OUTSIDE_INPUT_ROOT


def test_absolute_input_requires_an_explicit_read_root(tmp_path: Path) -> None:
    workspace, config_path = _workspace(tmp_path)
    inside = workspace.root / "data.jsonl"
    inside.write_text("{}\n", encoding="utf-8")
    outside = tmp_path / "outside.jsonl"
    outside.write_text("{}\n", encoding="utf-8")

    assert (
        workspace.path_policy.resolve_input(
            inside,
            base_dir=config_path.parent,
            field_name="input",
        )
        == inside
    )
    with pytest.raises(RuntimePathError) as exc:
        workspace.path_policy.resolve_input(
            outside,
            base_dir=config_path.parent,
            field_name="input",
        )

    assert exc.value.code is RuntimePathErrorCode.OUTSIDE_INPUT_ROOT


def test_additional_read_root_allows_explicit_external_input(tmp_path: Path) -> None:
    workspace, config_path = _workspace(tmp_path)
    external_root = tmp_path / "approved-inputs"
    external_root.mkdir()
    external = external_root / "data.jsonl"
    external.write_text("{}\n", encoding="utf-8")
    policy = RuntimePathPolicy(
        workspace_root=workspace.root,
        config_root=workspace.configs_root,
        artifacts_root=workspace.artifacts_root,
        results_root=workspace.results_root,
        additional_read_roots=(external_root,),
    )

    assert (
        policy.resolve_input(
            external,
            base_dir=config_path.parent,
            field_name="input",
        )
        == external
    )


def test_outputs_cannot_escape_managed_artifacts_root(tmp_path: Path) -> None:
    workspace, _ = _workspace(tmp_path)

    with pytest.raises(RuntimePathError) as traversal:
        workspace.path_policy.resolve_output(
            "../outside",
            field_name="pipeline.inference.save_dir",
        )
    with pytest.raises(RuntimePathError) as absolute:
        workspace.path_policy.resolve_output(
            tmp_path / "outside",
            field_name="pipeline.inference.save_dir",
        )

    assert traversal.value.code is RuntimePathErrorCode.OUTSIDE_ARTIFACTS_ROOT
    assert absolute.value.code is RuntimePathErrorCode.OUTSIDE_ARTIFACTS_ROOT


def test_symlink_escape_is_rejected_after_resolution(tmp_path: Path) -> None:
    workspace, config_path = _workspace(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "data.jsonl").write_text("{}\n", encoding="utf-8")
    link = config_path.parent / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")

    with pytest.raises(RuntimePathError) as error:
        workspace.path_policy.resolve_input(
            "linked/data.jsonl",
            base_dir=config_path.parent,
            field_name="input",
        )

    assert error.value.code is RuntimePathErrorCode.OUTSIDE_INPUT_ROOT


def test_artifact_cache_cannot_allocate_through_symlink_escape(
    tmp_path: Path,
) -> None:
    workspace, config_path = _workspace(tmp_path)
    suite_root = workspace.results_root / "suite-a"
    suite_root.mkdir(parents=True)
    outside = tmp_path / "outside-cache"
    outside.mkdir()
    cache_link = suite_root / "artifacts"
    try:
        cache_link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")

    with pytest.raises(RuntimePathError) as error:
        prepare_artifact_plan(
            ctx={
                "suite_root": suite_root,
                "config_path": config_path,
                "artifacts_root": workspace.artifacts_root,
                "behavior_name": "behavior",
                "behavior": "description",
                "context": None,
                "path_policy": workspace.path_policy,
            },
            stage_name="systematize",
            raw_cfg={},
            forced=False,
        )

    assert error.value.code is RuntimePathErrorCode.OUTSIDE_ARTIFACTS_ROOT
    assert not any(outside.iterdir())


def test_runtime_context_rejects_suite_link_to_another_suite(
    tmp_path: Path,
) -> None:
    workspace, config_path = _workspace(tmp_path)
    workspace.results_root.mkdir(parents=True)
    other_suite = workspace.results_root / "suite-b"
    other_suite.mkdir()
    suite_link = workspace.results_root / "suite-a"
    try:
        suite_link.symlink_to(other_suite, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")

    with pytest.raises(RuntimePathError) as error:
        load_runtime_context(
            _minimal_config(),
            config_path,
            stage_modules=STAGES,
            path_policy=workspace.path_policy,
        )

    assert error.value.code is RuntimePathErrorCode.MANAGED_PATH_LINK


def test_artifact_cache_rejects_cross_suite_links_for_mutations(
    tmp_path: Path,
) -> None:
    workspace, config_path = _workspace(tmp_path)
    suite_a = workspace.results_root / "suite-a"
    suite_b = workspace.results_root / "suite-b"
    suite_a.mkdir(parents=True)
    suite_b.mkdir()
    ctx = {
        "suite_root": suite_a,
        "config_path": config_path,
        "artifacts_root": workspace.artifacts_root,
        "behavior_name": "behavior",
        "behavior": "description",
        "context": None,
        "path_policy": workspace.path_policy,
    }

    protected_latest = suite_b / "protected-latest.json"
    protected_latest.write_text('{"protected": true}\n', encoding="utf-8")
    latest_link = suite_a / "latest.json"
    try:
        latest_link.symlink_to(protected_latest)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")

    with pytest.raises(RuntimePathError) as latest_error:
        update_latest(ctx, "systematize", {"version": "v0001"})
    assert latest_error.value.code is RuntimePathErrorCode.OUTSIDE_EXPECTED_ROOT
    assert protected_latest.read_text(encoding="utf-8") == '{"protected": true}\n'

    latest_link.unlink()
    plan = prepare_artifact_plan(
        ctx=ctx,
        stage_name="systematize",
        raw_cfg={},
        forced=False,
    )
    source = plan.output_paths["taxonomy"]
    source.write_text('{"safe": true}\n', encoding="utf-8")
    protected_copy = suite_b / "protected-taxonomy.json"
    protected_copy.write_text('{"protected": true}\n', encoding="utf-8")
    compatibility_link = suite_a / source.name
    compatibility_link.symlink_to(protected_copy)

    with pytest.raises(RuntimePathError) as copy_error:
        refresh_compatibility_files(
            ctx,
            "systematize",
            plan.output_paths,
        )
    assert copy_error.value.code is RuntimePathErrorCode.OUTSIDE_EXPECTED_ROOT
    assert protected_copy.read_text(encoding="utf-8") == '{"protected": true}\n'

    compatibility_link.unlink()
    protected_dir = suite_b / "protected-version"
    protected_dir.mkdir()
    protected_file = protected_dir / "keep.txt"
    protected_file.write_text("keep\n", encoding="utf-8")
    source.unlink()
    plan.artifact_dir.rmdir()
    plan.artifact_dir.symlink_to(protected_dir, target_is_directory=True)

    discard_artifact_plan(ctx, plan)

    assert protected_file.read_text(encoding="utf-8") == "keep\n"


def test_runtime_context_forces_managed_roots(tmp_path: Path) -> None:
    workspace, config_path = _workspace(tmp_path)
    raw = {
        **_minimal_config(),
        "artifacts_root": "custom-artifacts",
    }

    with pytest.raises(RuntimePathError) as exc:
        load_runtime_context(
            raw,
            config_path,
            stage_modules=STAGES,
            path_policy=workspace.path_policy,
        )

    assert exc.value.code is RuntimePathErrorCode.MANAGED_ROOT_OVERRIDE


def test_runtime_context_accepts_explicit_managed_roots(tmp_path: Path) -> None:
    workspace, config_path = _workspace(tmp_path)
    raw = {
        **_minimal_config(),
        "artifacts_root": "artifacts",
        "results_dir": "results",
    }

    context = load_runtime_context(
        raw,
        config_path,
        stage_modules=STAGES,
        path_policy=workspace.path_policy,
    )

    assert context["artifacts_root"] == workspace.artifacts_root
    assert context["results_dir"] == workspace.results_root
    assert context["path_policy"] is workspace.path_policy


def test_runtime_context_confines_run_outputs_to_current_run(
    tmp_path: Path,
) -> None:
    workspace, config_path = _workspace(tmp_path)
    other_run = workspace.results_root / "suite-a" / "run-b"
    raw = {
        "suite": "suite-a",
        "run": "run-a",
        "pipeline": {
            "inference": {
                "target": {"callable": "agent:run"},
                "save_dir": str(other_run),
            }
        },
    }

    with pytest.raises(RuntimePathError) as error:
        load_runtime_context(
            raw,
            config_path,
            stage_modules=STAGES,
            path_policy=workspace.path_policy,
        )

    assert error.value.code is RuntimePathErrorCode.OUTSIDE_EXPECTED_ROOT


def test_managed_tree_rejects_cross_run_file_link(tmp_path: Path) -> None:
    workspace, _ = _workspace(tmp_path)
    run_a = workspace.results_root / "suite-a" / "run-a"
    run_b = workspace.results_root / "suite-a" / "run-b"
    run_a.mkdir(parents=True)
    run_b.mkdir()
    protected = run_b / "inference_set.jsonl"
    protected.write_text('{"protected": true}\n', encoding="utf-8")
    output_link = run_a / "inference_set.jsonl"
    try:
        output_link.symlink_to(protected)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")

    with pytest.raises(RuntimePathError) as error:
        workspace.path_policy.require_managed_tree(
            run_a,
            field_name="run output",
            expected_root=run_a,
        )

    assert error.value.code is RuntimePathErrorCode.OUTSIDE_EXPECTED_ROOT
    assert protected.read_text(encoding="utf-8") == '{"protected": true}\n'


def test_every_explicit_stage_path_is_validated(tmp_path: Path) -> None:
    workspace, config_path = _workspace(tmp_path)
    raw = _minimal_config()
    raw["pipeline"]["inference"]["file_path"] = "../outside.jsonl"

    with pytest.raises(RuntimePathError) as exc:
        load_runtime_context(
            raw,
            config_path,
            stage_modules=STAGES,
            path_policy=workspace.path_policy,
        )

    assert exc.value.code is RuntimePathErrorCode.OUTSIDE_INPUT_ROOT


def test_toolset_is_revalidated_when_actually_loaded(tmp_path: Path) -> None:
    workspace, config_path = _workspace(tmp_path)
    outside = tmp_path / "tools.json"
    outside.write_text("[]\n", encoding="utf-8")

    with pytest.raises(RuntimePathError) as exc:
        _build_hosted_session(
            model="mock/model",
            tools_config={
                "_config_path": str(config_path),
                "toolset": str(outside),
                "simulator": "mock/simulator",
            },
            scenario={},
            generate_options=GenerateOptions(),
            max_tool_calls=1,
            synthetic_prompt_template="{scenario}",
            path_policy=workspace.path_policy,
        )

    assert exc.value.code is RuntimePathErrorCode.OUTSIDE_INPUT_ROOT


def test_strict_dynamic_import_searches_only_workspace(tmp_path: Path) -> None:
    workspace, config_path = _workspace(tmp_path)
    module_name = f"workspace_agent_{uuid.uuid4().hex[:8]}"
    module_path = workspace.root / f"{module_name}.py"
    module_path.write_text("def run(message):\n    return message\n", encoding="utf-8")

    module = import_callable_module(
        module_name,
        config_path=config_path,
        path_policy=workspace.path_policy,
    )

    assert module.run("ok") == "ok"


def test_strict_dynamic_import_does_not_fall_back_to_cwd(tmp_path: Path) -> None:
    workspace, config_path = _workspace(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    module_name = f"outside_agent_{uuid.uuid4().hex[:8]}"
    (outside / f"{module_name}.py").write_text(
        "def run(message):\n    return message\n",
        encoding="utf-8",
    )
    original_cwd = Path.cwd()
    os.chdir(outside)
    try:
        with pytest.raises(ValueError, match="inside the configured workspace"):
            import_callable_module(
                module_name,
                config_path=config_path,
                path_policy=workspace.path_policy,
            )
    finally:
        os.chdir(original_cwd)


def test_strict_module_import_supports_package_relative_imports(
    tmp_path: Path,
) -> None:
    existing_modules = set(sys.modules)
    workspace, config_path = _workspace(tmp_path)
    package_name = "strict_runtime_package"
    package = config_path.parent / package_name
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "helpers.py").write_text("VALUE = 'workspace'\n", encoding="utf-8")
    (package / "agent.py").write_text(
        "from .helpers import VALUE\n",
        encoding="utf-8",
    )

    try:
        module = import_callable_module(
            f"{package_name}.agent",
            config_path=config_path,
            path_policy=workspace.path_policy,
        )
        assert module.VALUE == "workspace"
    finally:
        for name in tuple(sys.modules):
            if name == package_name or name.startswith(f"{package_name}."):
                sys.modules.pop(name, None)
        _remove_workspace_imports(existing_modules)


def test_strict_module_import_isolates_identical_names_by_config_root(
    tmp_path: Path,
) -> None:
    existing_modules = set(sys.modules)
    workspace, _ = _workspace(tmp_path)
    first_root = workspace.configs_root / "first"
    second_root = workspace.configs_root / "second"
    first_root.mkdir()
    second_root.mkdir()
    first_config = first_root / "eval_config.yaml"
    second_config = second_root / "eval_config.yaml"
    first_config.write_text("pipeline: {}\n", encoding="utf-8")
    second_config.write_text("pipeline: {}\n", encoding="utf-8")
    (first_root / "helpers.py").write_text("VALUE = 'first'\n", encoding="utf-8")
    (second_root / "helpers.py").write_text("VALUE = 'second'\n", encoding="utf-8")
    (first_root / "agent.py").write_text(
        "def get_value():\n    import helpers\n    return helpers.VALUE\n",
        encoding="utf-8",
    )
    (second_root / "agent.py").write_text(
        "def get_value():\n    import helpers\n    return helpers.VALUE\n",
        encoding="utf-8",
    )

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first_future = executor.submit(
                import_callable_module,
                "agent",
                config_path=first_config,
                path_policy=workspace.path_policy,
            )
            second_future = executor.submit(
                import_callable_module,
                "agent",
                config_path=second_config,
                path_policy=workspace.path_policy,
            )
            first = first_future.result()
            second = second_future.result()

        assert first.get_value() == "first"
        assert second.get_value() == "second"
        assert first.__name__ != second.__name__
    finally:
        _remove_workspace_imports(existing_modules)


def test_strict_module_import_isolates_absolute_package_imports(
    tmp_path: Path,
) -> None:
    existing_modules = set(sys.modules)
    workspace, _ = _workspace(tmp_path)
    package_name = "shared_runtime_package"
    modules: list[object] = []

    try:
        for config_name, value in (("first", "first"), ("second", "second")):
            config_root = workspace.configs_root / config_name
            package = config_root / package_name
            package.mkdir(parents=True)
            config_path = config_root / "eval_config.yaml"
            config_path.write_text("pipeline: {}\n", encoding="utf-8")
            (package / "__init__.py").write_text("", encoding="utf-8")
            (package / "helpers.py").write_text(
                f"VALUE = {value!r}\n",
                encoding="utf-8",
            )
            (package / "agent.py").write_text(
                f"from {package_name}.helpers import VALUE\n",
                encoding="utf-8",
            )
            modules.append(
                import_callable_module(
                    f"{package_name}.agent",
                    config_path=config_path,
                    path_policy=workspace.path_policy,
                )
            )

        assert [module.VALUE for module in modules] == ["first", "second"]
    finally:
        _remove_workspace_imports(existing_modules)


def test_strict_direct_module_path_uses_isolated_lazy_imports(
    tmp_path: Path,
) -> None:
    existing_modules = set(sys.modules)
    workspace, config_path = _workspace(tmp_path)
    helper_name = "strict_direct_helper"
    (config_path.parent / f"{helper_name}.py").write_text(
        "VALUE = 'workspace'\n",
        encoding="utf-8",
    )
    module_path = config_path.parent / "direct_tools.py"
    module_path.write_text(
        "import importlib\n"
        "from importlib import import_module\n\n"
        f"def get_value():\n    return importlib.import_module({helper_name!r}).VALUE\n\n"
        f"def get_value_from_import():\n    return import_module({helper_name!r}).VALUE\n",
        encoding="utf-8",
    )

    external_root = tmp_path / "external-direct"
    external_root.mkdir()
    (external_root / f"{helper_name}.py").write_text(
        "VALUE = 'external'\n",
        encoding="utf-8",
    )
    original_sys_path = list(sys.path)
    sys.path.insert(0, str(external_root))
    try:
        importlib.import_module(helper_name)
        module = load_tool_module(
            str(module_path),
            config_path=config_path,
            path_policy=workspace.path_policy,
        )
        assert module.get_value() == "workspace"
        assert module.get_value_from_import() == "workspace"
        assert sys.path == [str(external_root), *original_sys_path]
    finally:
        sys.path[:] = original_sys_path
        sys.modules.pop(helper_name, None)
        _remove_workspace_imports(existing_modules)


def test_strict_direct_package_initializer_uses_workspace_module_name(
    tmp_path: Path,
) -> None:
    existing_modules = set(sys.modules)
    workspace, config_path = _workspace(tmp_path)
    package_init = config_path.parent / "__init__.py"
    package_init.write_text("VALUE = 'package'\n", encoding="utf-8")

    try:
        module = load_tool_module(
            str(package_init),
            config_path=config_path,
            path_policy=workspace.path_policy,
        )
        assert module.VALUE == "package"
    finally:
        _remove_workspace_imports(existing_modules)


def test_strict_module_import_ignores_preloaded_external_package(
    tmp_path: Path,
) -> None:
    existing_modules = set(sys.modules)
    workspace, config_path = _workspace(tmp_path)
    package_name = "strict_external_package"
    workspace_package = config_path.parent / package_name
    workspace_package.mkdir()
    (workspace_package / "__init__.py").write_text("", encoding="utf-8")
    (workspace_package / "helpers.py").write_text(
        "VALUE = 'workspace'\n",
        encoding="utf-8",
    )
    (workspace_package / "agent.py").write_text(
        f"from {package_name}.helpers import VALUE\n",
        encoding="utf-8",
    )

    external_root = tmp_path / "external"
    external_package = external_root / package_name
    external_package.mkdir(parents=True)
    (external_package / "__init__.py").write_text("", encoding="utf-8")
    (external_package / "helpers.py").write_text(
        "VALUE = 'external'\n",
        encoding="utf-8",
    )

    sys.path.insert(0, str(external_root))
    try:
        importlib.import_module(package_name)
        module = import_callable_module(
            f"{package_name}.agent",
            config_path=config_path,
            path_policy=workspace.path_policy,
        )
        assert module.VALUE == "workspace"
    finally:
        sys.path.remove(str(external_root))
        for name in tuple(sys.modules):
            if name == package_name or name.startswith(f"{package_name}."):
                sys.modules.pop(name, None)
        _remove_workspace_imports(existing_modules)


def test_direct_module_and_sys_path_must_stay_in_workspace(tmp_path: Path) -> None:
    workspace, config_path = _workspace(tmp_path)
    outside_module = tmp_path / "outside_tools.py"
    outside_module.write_text("class Tools:\n    pass\n", encoding="utf-8")

    with pytest.raises(RuntimePathError) as module_error:
        load_tool_module(
            str(outside_module),
            config_path=config_path,
            path_policy=workspace.path_policy,
        )
    with pytest.raises(RuntimePathError) as sys_path_error:
        validate_sys_path_addition(
            tmp_path,
            config_path=config_path,
            path_policy=workspace.path_policy,
        )

    assert module_error.value.code is RuntimePathErrorCode.OUTSIDE_WORKSPACE
    assert sys_path_error.value.code is RuntimePathErrorCode.OUTSIDE_WORKSPACE


def test_legacy_absolute_inputs_remain_supported(tmp_path: Path) -> None:
    absolute = tmp_path / "outside.jsonl"
    resolved = _resolve_path(
        absolute,
        artifacts_root=tmp_path / "artifacts",
        cfg_dir=tmp_path / "configs",
    )

    assert Path(resolved) == absolute.resolve()
