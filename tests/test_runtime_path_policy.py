# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from assert_ai.core.runtime_path_policy import (
    RuntimePathError,
    RuntimePathErrorCode,
    RuntimePathPolicy,
    _is_within,
    _windows_unsafe_output_component,
)
from assert_ai.core.workspace import WorkspaceService


def _workspace(
    tmp_path: Path,
    *,
    additional_read_roots: tuple[Path, ...] = (),
) -> tuple[WorkspaceService, Path]:
    root = tmp_path / "workspace"
    configs = root / "evals"
    configs.mkdir(parents=True)
    config_path = configs / "eval_config.yaml"
    config_path.write_text("pipeline: {}\n", encoding="utf-8")
    return (
        WorkspaceService.create(
            root,
            additional_read_roots=additional_read_roots,
        ),
        config_path,
    )


def test_workspace_service_exposes_canonical_relative_roots(
    tmp_path: Path,
) -> None:
    workspace, _ = _workspace(tmp_path)

    assert workspace.reference(workspace.root) == "."
    assert workspace.reference(workspace.configs_root) == "evals"
    assert workspace.reference(workspace.artifacts_root) == "artifacts"
    assert workspace.reference(workspace.results_root) == "artifacts/results"


def test_workspace_reference_rejects_external_paths(tmp_path: Path) -> None:
    workspace, _ = _workspace(tmp_path)

    with pytest.raises(RuntimePathError) as exc:
        workspace.reference(tmp_path / "outside")

    assert exc.value.code is RuntimePathErrorCode.OUTSIDE_WORKSPACE
    assert exc.value.field_name == "workspace reference"


def test_workspace_reference_anchors_relative_paths_to_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, config_path = _workspace(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    assert workspace.reference("evals/eval_config.yaml") == "evals/eval_config.yaml"
    assert (
        workspace.path_policy.require_workspace_path(
            "evals/eval_config.yaml",
            field_name="config",
        )
        == config_path
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows case semantics")
def test_workspace_paths_preserve_mixed_case_components(tmp_path: Path) -> None:
    workspace, _ = _workspace(tmp_path)
    mixed_case_config = workspace.configs_root / "CamelCase.YAML"
    mixed_case_config.write_text("pipeline: {}\n", encoding="utf-8")
    reference = r"evals\CamelCase.YAML"

    assert (
        workspace.path_policy.require_workspace_path(
            reference,
            field_name="config",
        )
        == mixed_case_config
    )
    assert (
        workspace.path_policy.workspace_reference(reference)
        == "evals/CamelCase.YAML"
    )
    assert (
        workspace.path_policy.resolve_config_path(
            reference,
            must_exist=True,
        )
        == mixed_case_config
    )


def test_workspace_requires_an_existing_root(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        WorkspaceService.create(tmp_path / "missing")


@pytest.mark.skipif(os.name != "nt", reason="Windows path representation")
def test_extended_length_path_is_compared_as_the_same_windows_path() -> None:
    root = Path("C:/workspace")

    assert _is_within(
        Path("//?/C:/workspace/artifacts/results"),
        root,
    )
    assert not _is_within(Path("//?/C:/outside"), root)


@pytest.mark.skipif(os.name != "nt", reason="Windows path representation")
def test_extended_length_public_paths_use_canonical_operations(
    tmp_path: Path,
) -> None:
    workspace, config_path = _workspace(tmp_path)
    extended_config = Path(f"\\\\?\\{config_path}")
    extended_artifacts = Path(f"\\\\?\\{workspace.artifacts_root}")

    assert workspace.reference(extended_config) == "evals/eval_config.yaml"
    workspace.path_policy.require_managed_root(
        extended_artifacts,
        workspace.artifacts_root,
        field_name="artifacts_root",
    )
    assert workspace.path_policy.module_search_roots(extended_config) == (
        ("Relative to config", workspace.configs_root),
        ("Relative to workspace", workspace.root),
    )


def test_policy_anchors_relative_roots_to_workspace(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()

    policy = RuntimePathPolicy(
        workspace_root=root,
        config_root=Path("evals"),
        artifacts_root=Path("artifacts"),
        results_root=Path("artifacts/results"),
        additional_read_roots=(Path("shared"),),
    )

    assert policy.config_root == root / "evals"
    assert policy.artifacts_root == root / "artifacts"
    assert policy.results_root == root / "artifacts" / "results"
    assert policy.additional_read_roots == (root / "shared",)


def test_policy_rejects_managed_roots_outside_workspace(
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()

    with pytest.raises(RuntimePathError) as exc:
        RuntimePathPolicy(
            workspace_root=root,
            config_root=tmp_path / "evals",
            artifacts_root=root / "artifacts",
            results_root=root / "artifacts" / "results",
        )

    assert exc.value.code is RuntimePathErrorCode.INVALID_ROOT
    assert exc.value.field_name == "config_root"


@pytest.mark.parametrize(
    ("field_name", "relative_path"),
    [
        ("config_root", "evals"),
        ("artifacts_root", "artifacts"),
        ("results_root", "artifacts/results"),
        ("additional_read_roots[0]", "shared"),
    ],
)
def test_policy_rejects_existing_files_as_roots(
    tmp_path: Path,
    field_name: str,
    relative_path: str,
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    file_root = root / relative_path
    file_root.parent.mkdir(parents=True, exist_ok=True)
    file_root.write_text("not a directory", encoding="utf-8")
    config_root = file_root if field_name == "config_root" else root / "evals"
    artifacts_root = (
        file_root if field_name == "artifacts_root" else root / "artifacts"
    )
    results_root = (
        file_root
        if field_name == "results_root"
        else root / "artifacts" / "results"
    )
    additional_read_roots = (
        (file_root,) if field_name.startswith("additional_read_roots") else ()
    )

    with pytest.raises(RuntimePathError) as exc:
        RuntimePathPolicy(
            workspace_root=root,
            config_root=config_root,
            artifacts_root=artifacts_root,
            results_root=results_root,
            additional_read_roots=additional_read_roots,
        )

    assert exc.value.code is RuntimePathErrorCode.INVALID_ROOT
    assert exc.value.field_name == field_name


@pytest.mark.parametrize(
    ("config_root", "artifacts_root"),
    [
        ("shared", "shared"),
        ("shared", "shared/artifacts"),
        ("artifacts/evals", "artifacts"),
    ],
)
def test_config_and_artifacts_roots_must_be_disjoint(
    tmp_path: Path,
    config_root: str,
    artifacts_root: str,
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()

    with pytest.raises(RuntimePathError) as exc:
        RuntimePathPolicy(
            workspace_root=root,
            config_root=Path(config_root),
            artifacts_root=Path(artifacts_root),
            results_root=Path(artifacts_root) / "results",
        )

    assert exc.value.code is RuntimePathErrorCode.INVALID_ROOT
    assert exc.value.field_name == "config_root"


@pytest.mark.parametrize("force_managed_outputs", [False, True])
def test_results_root_must_be_strictly_below_artifacts_root(
    tmp_path: Path,
    force_managed_outputs: bool,
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()

    with pytest.raises(RuntimePathError) as exc:
        RuntimePathPolicy(
            workspace_root=root,
            config_root=Path("evals"),
            artifacts_root=Path("artifacts"),
            results_root=Path("artifacts"),
            force_managed_outputs=force_managed_outputs,
        )

    assert exc.value.code is RuntimePathErrorCode.INVALID_ROOT
    assert exc.value.field_name == "results_root"


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink semantics")
def test_root_topology_rejects_posix_symlink_alias(
    tmp_path: Path,
    symlink_or_skip,
) -> None:
    root = tmp_path / "workspace"
    artifacts = root / "artifacts"
    artifacts.mkdir(parents=True)
    config_alias = root / "evals"
    symlink_or_skip(config_alias, artifacts)

    with pytest.raises(RuntimePathError) as exc:
        RuntimePathPolicy(
            workspace_root=root,
            config_root=config_alias,
            artifacts_root=artifacts,
            results_root=artifacts / "results",
        )

    assert exc.value.code is RuntimePathErrorCode.INVALID_ROOT
    assert exc.value.field_name == "config_root"


@pytest.mark.skipif(os.name != "nt", reason="Windows junction semantics")
def test_root_topology_rejects_windows_junction_alias(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    artifacts = root / "artifacts"
    artifacts.mkdir(parents=True)
    config_alias = root / "evals"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(config_alias), str(artifacts)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip(f"Cannot create test junction: {result.stderr.strip()}")

    try:
        with pytest.raises(RuntimePathError) as exc:
            RuntimePathPolicy(
                workspace_root=root,
                config_root=config_alias,
                artifacts_root=artifacts,
                results_root=artifacts / "results",
            )
        assert exc.value.code is RuntimePathErrorCode.INVALID_ROOT
        assert exc.value.field_name == "config_root"
    finally:
        config_alias.rmdir()


def test_config_paths_are_contained_under_config_root(
    tmp_path: Path,
) -> None:
    workspace, config_path = _workspace(tmp_path)

    assert (
        workspace.path_policy.resolve_config_path("eval_config.yaml")
        == config_path
    )
    assert (
        workspace.path_policy.resolve_config_path("evals/eval_config.yaml")
        == config_path
    )

    for candidate in ("../outside.yaml", tmp_path / "outside.yaml"):
        with pytest.raises(RuntimePathError) as exc:
            workspace.path_policy.resolve_config_path(candidate)
        assert exc.value.code is RuntimePathErrorCode.OUTSIDE_CONFIG_ROOT


def test_config_path_can_require_an_existing_file(tmp_path: Path) -> None:
    workspace, _ = _workspace(tmp_path)
    directory = workspace.configs_root / "nested"
    directory.mkdir()

    with pytest.raises(RuntimePathError) as missing:
        workspace.path_policy.resolve_config_path(
            "missing.yaml",
            must_exist=True,
        )
    with pytest.raises(RuntimePathError) as not_file:
        workspace.path_policy.resolve_config_path(
            "nested",
            must_exist=True,
        )

    assert missing.value.code is RuntimePathErrorCode.PATH_NOT_FOUND
    assert not_file.value.code is RuntimePathErrorCode.NOT_A_FILE


def test_relative_input_cannot_escape_its_base_directory(
    tmp_path: Path,
) -> None:
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


def test_artifact_relative_input_uses_artifacts_root(tmp_path: Path) -> None:
    workspace, config_path = _workspace(tmp_path)
    artifact = workspace.artifacts_root / "inputs" / "cases.jsonl"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("{}\n", encoding="utf-8")

    assert (
        workspace.path_policy.resolve_input(
            "artifacts/inputs/cases.jsonl",
            base_dir=config_path.parent,
            field_name="test cases",
            must_exist=True,
            file_only=True,
        )
        == artifact
    )


def test_absolute_input_requires_an_approved_read_root(
    tmp_path: Path,
) -> None:
    external_root = tmp_path / "approved-inputs"
    external_root.mkdir()
    external = external_root / "cases.jsonl"
    external.write_text("{}\n", encoding="utf-8")
    workspace, config_path = _workspace(tmp_path)

    with pytest.raises(RuntimePathError) as exc:
        workspace.path_policy.resolve_input(
            external,
            base_dir=config_path.parent,
            field_name="test cases",
        )
    assert exc.value.code is RuntimePathErrorCode.OUTSIDE_INPUT_ROOT

    approved, approved_config = _workspace(
        tmp_path / "approved",
        additional_read_roots=(external_root,),
    )
    assert (
        approved.path_policy.resolve_input(
            external,
            base_dir=approved_config.parent,
            field_name="test cases",
            must_exist=True,
        )
        == external
    )


def test_legacy_policy_can_allow_external_absolute_inputs(
    tmp_path: Path,
) -> None:
    workspace, config_path = _workspace(tmp_path)
    external = tmp_path / "external.jsonl"
    external.write_text("{}\n", encoding="utf-8")
    policy = RuntimePathPolicy(
        workspace_root=workspace.root,
        config_root=workspace.configs_root,
        artifacts_root=workspace.artifacts_root,
        results_root=workspace.results_root,
        allow_absolute_inputs=True,
    )

    assert (
        policy.resolve_input(
            external,
            base_dir=config_path.parent,
            field_name="legacy input",
            must_exist=True,
        )
        == external
    )


def test_outputs_are_contained_under_artifacts_root(tmp_path: Path) -> None:
    workspace, _ = _workspace(tmp_path)

    assert (
        workspace.path_policy.resolve_output(
            "reports/summary.json",
            field_name="report",
        )
        == workspace.artifacts_root / "reports" / "summary.json"
    )
    assert (
        workspace.path_policy.resolve_output(
            "artifacts/results/suite-a",
            field_name="suite",
        )
        == workspace.results_root / "suite-a"
    )

    for candidate in ("../outside", tmp_path / "outside"):
        with pytest.raises(RuntimePathError) as exc:
            workspace.path_policy.resolve_output(
                candidate,
                field_name="report",
            )
        assert exc.value.code is RuntimePathErrorCode.OUTSIDE_ARTIFACTS_ROOT


@pytest.mark.parametrize(
    "candidate",
    [
        "NUL",
        "nul.txt",
        "reports/COM1.log",
        "reports/LPT².json",
        "name:stream",
        "C:drive-relative",
        "forbidden<name",
        "control\x1fchar",
        "trailing.",
        "trailing ",
    ],
)
def test_windows_unsafe_output_components_are_detected_deterministically(
    candidate: str,
) -> None:
    assert _windows_unsafe_output_component(candidate) is not None


@pytest.mark.parametrize(
    "candidate",
    [
        "normal",
        "nulled.txt",
        "COM10.log",
        "name.with.dots",
        "space inside",
    ],
)
def test_windows_valid_output_components_are_not_over_rejected(
    candidate: str,
) -> None:
    assert _windows_unsafe_output_component(candidate) is None


@pytest.mark.skipif(os.name != "nt", reason="Windows output semantics")
@pytest.mark.parametrize(
    "candidate",
    [
        "NUL.txt",
        "reports/COM1.json",
        "name:stream",
        "C:drive-relative",
        "forbidden|name",
        "control\x07char",
        "trailing.",
        "trailing ",
    ],
)
def test_all_managed_output_resolvers_reject_invalid_windows_names(
    tmp_path: Path,
    candidate: str,
) -> None:
    workspace, _ = _workspace(tmp_path)

    with pytest.raises(RuntimePathError) as output_exc:
        workspace.path_policy.resolve_output(candidate, field_name="output")
    with pytest.raises(RuntimePathError) as managed_exc:
        workspace.path_policy.resolve_managed_output(
            candidate,
            field_name="managed output",
            expected_root=workspace.results_root / "suite-a",
        )

    assert output_exc.value.code is RuntimePathErrorCode.INVALID_OUTPUT_PATH
    assert managed_exc.value.code is RuntimePathErrorCode.INVALID_OUTPUT_PATH


@pytest.mark.skipif(os.name != "nt", reason="Windows output semantics")
def test_managed_output_rejects_invalid_windows_expected_root(
    tmp_path: Path,
) -> None:
    workspace, _ = _workspace(tmp_path)

    with pytest.raises(RuntimePathError) as exc:
        workspace.path_policy.resolve_managed_output(
            "result.json",
            field_name="managed output",
            expected_root="NUL",
        )

    assert exc.value.code is RuntimePathErrorCode.INVALID_OUTPUT_PATH
    assert exc.value.field_name == "managed output expected root"


@pytest.mark.skipif(os.name == "nt", reason="POSIX output semantics")
def test_posix_output_resolver_preserves_posix_valid_names(tmp_path: Path) -> None:
    workspace, _ = _workspace(tmp_path)

    for candidate in ("NUL.txt", "name:stream", "trailing."):
        assert workspace.path_policy.resolve_output(
            candidate,
            field_name="output",
        ) == workspace.artifacts_root / candidate


@pytest.mark.skipif(os.name != "nt", reason="Windows case semantics")
def test_recognized_root_prefixes_are_case_insensitive_on_windows(
    tmp_path: Path,
) -> None:
    workspace, config_path = _workspace(tmp_path)

    assert (
        workspace.path_policy.resolve_config_path(
            "EVALS/eval_config.yaml",
            must_exist=True,
        )
        == config_path
    )
    assert (
        workspace.path_policy.resolve_output(
            "ARTIFACTS/results/suite-a",
            field_name="suite",
        )
        == workspace.results_root / "suite-a"
    )


def test_managed_output_is_confined_to_operation_root(
    tmp_path: Path,
) -> None:
    workspace, _ = _workspace(tmp_path)
    suite_root = workspace.results_root / "suite-a"

    assert (
        workspace.path_policy.resolve_managed_output(
            "run-a",
            field_name="run root",
            expected_root=suite_root,
        )
        == suite_root / "run-a"
    )

    with pytest.raises(RuntimePathError) as exc:
        workspace.path_policy.resolve_managed_output(
            workspace.results_root / "suite-b" / "run-b",
            field_name="run root",
            expected_root=suite_root,
        )

    assert exc.value.code is RuntimePathErrorCode.OUTSIDE_EXPECTED_ROOT


def test_workspace_file_resolution_checks_kind(tmp_path: Path) -> None:
    workspace, config_path = _workspace(tmp_path)

    assert (
        workspace.resolve_file("evals/eval_config.yaml", field_name="config")
        == config_path
    )

    with pytest.raises(RuntimePathError) as outside:
        workspace.resolve_file(
            tmp_path / "outside.yaml",
            field_name="config",
        )
    with pytest.raises(RuntimePathError) as directory:
        workspace.resolve_file("evals", field_name="config")

    assert outside.value.code is RuntimePathErrorCode.OUTSIDE_WORKSPACE
    assert directory.value.code is RuntimePathErrorCode.NOT_A_FILE


def test_config_path_can_reject_links_inside_config_root(
    tmp_path: Path,
    symlink_or_skip,
) -> None:
    workspace, config_path = _workspace(tmp_path)
    link = workspace.configs_root / "linked.yaml"
    symlink_or_skip(link, config_path)

    assert workspace.path_policy.resolve_config_path(link) == config_path
    with pytest.raises(RuntimePathError) as exc:
        workspace.path_policy.resolve_config_path(
            link,
            reject_links=True,
        )

    assert exc.value.code is RuntimePathErrorCode.MANAGED_PATH_LINK


def test_managed_output_can_reject_links(
    tmp_path: Path,
    symlink_or_skip,
) -> None:
    workspace, _ = _workspace(tmp_path)
    suite_root = workspace.results_root / "suite-a"
    suite_root.mkdir(parents=True)
    target = suite_root / "result.json"
    target.write_text("{}\n", encoding="utf-8")
    link = suite_root / "linked.json"
    symlink_or_skip(link, target)

    with pytest.raises(RuntimePathError) as exc:
        workspace.path_policy.resolve_managed_output(
            link,
            field_name="result",
            expected_root=suite_root,
            reject_links=True,
        )

    assert exc.value.code is RuntimePathErrorCode.MANAGED_PATH_LINK


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink semantics")
def test_reject_links_is_a_snapshot_not_atomic_write_authorization(
    tmp_path: Path,
) -> None:
    workspace, _ = _workspace(tmp_path)
    suite_root = workspace.results_root / "suite-a"
    suite_root.mkdir(parents=True)
    candidate = suite_root / "replaceable" / "result.json"

    authorized_snapshot = workspace.path_policy.resolve_managed_output(
        candidate,
        field_name="result",
        expected_root=suite_root,
        reject_links=True,
    )

    outside = tmp_path / "outside"
    outside.mkdir()
    candidate.parent.symlink_to(outside, target_is_directory=True)

    assert authorized_snapshot.resolve() == outside / "result.json"
    with pytest.raises(RuntimePathError) as exc:
        workspace.path_policy.resolve_managed_output(
            candidate,
            field_name="result",
            expected_root=suite_root,
            reject_links=True,
        )

    assert exc.value.code is RuntimePathErrorCode.OUTSIDE_ARTIFACTS_ROOT


def test_managed_tree_rejects_nested_links(
    tmp_path: Path,
    symlink_or_skip,
) -> None:
    workspace, _ = _workspace(tmp_path)
    suite_root = workspace.results_root / "suite-a"
    run_root = suite_root / "run-a"
    run_root.mkdir(parents=True)
    target = run_root / "scores.jsonl"
    target.write_text("{}\n", encoding="utf-8")
    symlink_or_skip(run_root / "scores-link.jsonl", target)

    with pytest.raises(RuntimePathError) as exc:
        workspace.path_policy.require_managed_tree(
            suite_root,
            field_name="suite tree",
            expected_root=workspace.results_root,
        )

    assert exc.value.code is RuntimePathErrorCode.MANAGED_PATH_LINK


def test_module_search_roots_stay_inside_workspace(tmp_path: Path) -> None:
    workspace, config_path = _workspace(tmp_path)

    assert workspace.path_policy.module_search_roots(config_path) == (
        ("Relative to config", workspace.configs_root),
        ("Relative to workspace", workspace.root),
    )

    with pytest.raises(RuntimePathError) as exc:
        workspace.path_policy.module_search_roots(tmp_path / "outside.yaml")

    assert exc.value.code is RuntimePathErrorCode.OUTSIDE_WORKSPACE


def test_managed_root_override_is_rejected(tmp_path: Path) -> None:
    workspace, _ = _workspace(tmp_path)

    workspace.path_policy.require_managed_root(
        workspace.artifacts_root,
        workspace.artifacts_root,
        field_name="artifacts_root",
    )
    with pytest.raises(RuntimePathError) as exc:
        workspace.path_policy.require_managed_root(
            workspace.results_root,
            workspace.artifacts_root,
            field_name="artifacts_root",
        )

    assert exc.value.code is RuntimePathErrorCode.MANAGED_ROOT_OVERRIDE
