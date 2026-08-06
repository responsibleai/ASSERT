# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Adapter that publishes a completed ASSERT run to Azure AI Foundry.

ASSERT writes each run to disk as an artifact tree (``taxonomy.json``,
``test_set.jsonl``, ``inference_set.jsonl``, ``scores.jsonl``,
``metrics.json``). Azure AI Foundry stores evaluation results as three
independent project assets:

- **Custom evaluators** — one per ASSERT judge dimension, registered in
  the project's evaluator catalog.
- **Dataset asset** — the scored rows, uploaded as a versioned Foundry
  dataset.
- **Eval + eval.run** — the definition that binds the dataset (as data
  source) to the registered evaluators (as testing criteria), and the
  run that materializes results.

This subpackage bridges the two. The exporter uses the first-party
`azure-ai-projects` SDK for every data-plane call and
``DefaultAzureCredential`` for auth (`az login`, workload identity,
or managed identity all work).

Two evaluator variants ship together:

- **Code-based** (`assert-{dim}`) — a Python `grade()` function runs in
  Foundry's sandbox and returns the pre-computed ASSERT score verbatim.
  Zero incremental judge cost, zero re-judgment noise.
- **Prompt-based** (`assert-{dim}-rescore`) — Foundry runs its own LLM
  judge against the ASSERT rubric prose for a second opinion. Costs
  one judge call per (row × dimension) and disagreements with ASSERT
  are expected.

Which variants get registered is picked per push via a CLI flag; both
render side-by-side in the Foundry UI when both are enabled.

Layered dependencies
--------------------
Every symbol is lazily imported so ``import assert_ai.integrations.foundry``
works even when the optional ``foundry`` extra is not installed. Pure
translation helpers (``artifacts``, ``dataset``, ``evaluators``) depend
only on ``assert_ai`` and the standard library; the ``pipeline`` module
requires ``azure-ai-projects`` and raises a clear install hint
(``pip install "assert-ai[foundry]"``) when the extra is absent.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - import-time typing only
    from assert_ai.integrations.foundry.artifacts import (
        AssertRun,
        AssertRunError,
        load_run,
        viewer_file_names,
    )
    from assert_ai.integrations.foundry.evaluators import (
        EVALUATOR_NAME_PREFIX,
        RESCORE_SUFFIX,
        AssertEvaluatorSpec,
        EvaluatorMode,
        EvaluatorSpecError,
        build_code_evaluator_spec,
        build_evaluator_specs_for_run,
        build_prompt_evaluator_spec,
        evaluator_name_for,
        resolve_rubric_prose,
    )
    from assert_ai.integrations.foundry.dataset import (
        DatasetRowsError,
        build_dataset_rows,
        content_hash,
        rows_to_jsonl_bytes,
    )
    from assert_ai.integrations.foundry.pipeline import (
        DATASET_NAME_PREFIX,
        DatasetRef,
        DryRunResult,
        EVAL_NAME_PREFIX,
        EvaluatorRef,
        PushError,
        PushResult,
        RUN_NAME_PREFIX,
        default_dataset_name,
        default_eval_name,
        default_run_name,
        push_run,
        push_run_dir,
        resolve_judge_deployment,
        strip_litellm_prefix,
    )


# Map each lazily-loaded public symbol to the submodule that defines it. The
# submodule import is what triggers the optional third-party dependency, so we
# defer it until the symbol is actually requested. Populated as later commits
# land the loader, evaluators, dataset row builder, pipeline, and CLI wiring.
_LAZY_EXPORTS: dict[str, str] = {
    "AssertRun": "artifacts",
    "AssertRunError": "artifacts",
    "load_run": "artifacts",
    "viewer_file_names": "artifacts",
    "AssertEvaluatorSpec": "evaluators",
    "EVALUATOR_NAME_PREFIX": "evaluators",
    "EvaluatorMode": "evaluators",
    "EvaluatorSpecError": "evaluators",
    "RESCORE_SUFFIX": "evaluators",
    "build_code_evaluator_spec": "evaluators",
    "build_evaluator_specs_for_run": "evaluators",
    "build_prompt_evaluator_spec": "evaluators",
    "evaluator_name_for": "evaluators",
    "resolve_rubric_prose": "evaluators",
    "DatasetRowsError": "dataset",
    "build_dataset_rows": "dataset",
    "content_hash": "dataset",
    "rows_to_jsonl_bytes": "dataset",
    "DATASET_NAME_PREFIX": "pipeline",
    "DatasetRef": "pipeline",
    "DryRunResult": "pipeline",
    "EVAL_NAME_PREFIX": "pipeline",
    "EvaluatorRef": "pipeline",
    "PushError": "pipeline",
    "PushResult": "pipeline",
    "RUN_NAME_PREFIX": "pipeline",
    "default_dataset_name": "pipeline",
    "default_eval_name": "pipeline",
    "default_run_name": "pipeline",
    "push_run": "pipeline",
    "push_run_dir": "pipeline",
    "resolve_judge_deployment": "pipeline",
    "strip_litellm_prefix": "pipeline",
}

# Human-readable install hints per submodule, surfaced when the optional
# dependency that backs a lazy symbol is missing.
_MISSING_DEPENDENCY_HINT: dict[str, str] = {
    "evaluators": (
        "The Foundry evaluator spec builder requires the 'azure-ai-projects' "
        "package. Install the Foundry extra with: pip install \"assert-ai[foundry]\""
    ),
    "pipeline": (
        "The Foundry pipeline requires the 'azure-ai-projects' and "
        "'azure-identity' packages. Install the Foundry extra with: "
        "pip install \"assert-ai[foundry]\""
    ),
}


__all__: list[str] = [
    "AssertRun",
    "AssertRunError",
    "load_run",
    "viewer_file_names",
    "AssertEvaluatorSpec",
    "EVALUATOR_NAME_PREFIX",
    "EvaluatorMode",
    "EvaluatorSpecError",
    "RESCORE_SUFFIX",
    "build_code_evaluator_spec",
    "build_evaluator_specs_for_run",
    "build_prompt_evaluator_spec",
    "evaluator_name_for",
    "resolve_rubric_prose",
    "DatasetRowsError",
    "build_dataset_rows",
    "content_hash",
    "rows_to_jsonl_bytes",
    "DATASET_NAME_PREFIX",
    "DatasetRef",
    "DryRunResult",
    "EVAL_NAME_PREFIX",
    "EvaluatorRef",
    "PushError",
    "PushResult",
    "RUN_NAME_PREFIX",
    "default_dataset_name",
    "default_eval_name",
    "default_run_name",
    "push_run",
    "push_run_dir",
    "resolve_judge_deployment",
    "strip_litellm_prefix",
]


def __getattr__(name: str) -> object:
    """Lazily import Foundry-backed symbols (PEP 562).

    Keeping these imports lazy means ``import assert_ai.integrations.foundry``
    works even when the optional Foundry packages are not installed. The
    optional dependency (``azure-ai-projects``) is only required when a
    caller actually reaches for a symbol whose submodule needs it.
    """
    submodule = _LAZY_EXPORTS.get(name)
    if submodule is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    try:
        module = importlib.import_module(f"{__name__}.{submodule}")
    except ModuleNotFoundError as exc:
        hint = _MISSING_DEPENDENCY_HINT.get(submodule)
        if hint is None:
            raise
        raise ModuleNotFoundError(hint, name=exc.name) from exc
    return getattr(module, name)


def __dir__() -> list[str]:
    return sorted(__all__)
