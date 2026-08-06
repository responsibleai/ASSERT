# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Tests for the SDK-backed Foundry push pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import pytest

# The `[foundry]` extra (azure-ai-projects) backs every symbol reached
# from this module. Skip cleanly on a base install so CI's Tier 1 job
# (which does not install optional extras) can still collect the file.
pytest.importorskip("azure.ai.projects")

from assert_ai.integrations.foundry.artifacts import AssertRun
from assert_ai.integrations.foundry.evaluators import (
    AssertEvaluatorSpec,
    build_code_evaluator_spec,
    build_prompt_evaluator_spec,
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
    _definition_fingerprint,
    default_dataset_name,
    default_eval_name,
    default_run_name,
    push_run,
    push_run_dir,
    resolve_judge_deployment,
    strip_litellm_prefix,
)


# ── Fixtures ─────────────────────────────────────────────────────────


_UNSET: Any = object()


def _make_run(
    *,
    suite_id: str = "helpful-qa",
    run_id: str = "run-1",
    config: Any = _UNSET,
    scores: Any = _UNSET,
) -> AssertRun:
    default_inference = [
        {
            "type": "prompt",
            "test_case_id": "tc-1",
            "behavior": "helpful",
            "events": [
                {"view": ["combined"], "edit": {"type": "add_message", "message": {"role": "user", "content": "Hi"}}},
                {"view": ["combined"], "edit": {"type": "add_message", "message": {"role": "assistant", "content": "Hello!"}}},
            ],
        }
    ]
    default_scores = [
        {
            "test_case_id": "tc-1",
            "judge_model": "azure/gpt-5.4-mini",
            "judge_status": "ok",
            "verdict": {
                "dimensions": {"policy_violation": False, "overrefusal": False},
                "dimension_justifications": {"policy_violation": "clean", "overrefusal": "responsive"},
            },
        }
    ]
    default_config: dict[str, Any] = {"default_model": {"name": "azure/gpt-5.4"}}
    return AssertRun(
        run_dir=Path("/tmp/r"),
        suite_dir=Path("/tmp/s"),
        suite_id=suite_id,
        run_id=run_id,
        taxonomy=None,
        systematization=None,
        stratification=None,
        suite_metadata=None,
        latest=None,
        test_set=(),
        config=default_config if config is _UNSET else config,
        inference_set=tuple(default_inference),
        scores=tuple(default_scores if scores is _UNSET else scores),
        metrics=None,
        manifest=None,
        artifacts_cache=None,
        inference_config_hash="abc123",
        judge_config_hash="def456",
        viewer_files={},
    )


# ── Fake SDK client ─────────────────────────────────────────────────
#
# The real AIProjectClient has three surfaces we exercise:
#   - client.beta.evaluators    → BetaEvaluatorsOperations
#   - client.datasets            → DatasetsOperations
#   - client.get_openai_client() → OpenAI client (evals + evals.runs)
#
# We fake all three at the method level, recording calls for
# assertions and returning shaped SDK-like values.


class _NotFound(KeyError):
    """Our stand-in for ResourceNotFoundError — pipeline treats KeyError as not-found."""


@dataclass
class _FakeEvaluators:
    existing: dict[tuple[str, str], Any] = field(default_factory=dict)
    created: list[tuple[str, Any]] = field(default_factory=list)
    deleted: list[tuple[str, str]] = field(default_factory=list)

    def get_version(self, name: str, version: str) -> Any:
        key = (name, version)
        if key not in self.existing:
            raise _NotFound(key)
        return self.existing[key]

    def list_versions(self, name: str, **_: Any) -> list[Any]:
        """Return every existing version for ``name`` in insertion order.

        Real SDK returns an ItemPaged; the pipeline consumes both
        eager iterables and pagers via :func:`_iter_paged`.
        """
        return [ev for (n, _v), ev in self.existing.items() if n == name]

    def create_version(self, name: str, evaluator_version: Any) -> Any:
        self.created.append((name, evaluator_version))
        # Honor whatever version the pipeline set on the payload —
        # the real service assigns the canonical version, but our
        # fake needs to reflect what the pipeline requested so
        # subsequent list_versions calls see it.
        version_value = getattr(evaluator_version, "version", None)
        version = str(version_value) if version_value else "1"
        self.existing[(name, version)] = evaluator_version
        return evaluator_version

    def delete_version(self, name: str, version: str) -> None:
        self.deleted.append((name, version))
        self.existing.pop((name, version), None)


@dataclass
class _FakeDataset:
    id: str


@dataclass
class _FakeDatasets:
    existing: dict[tuple[str, str], Any] = field(default_factory=dict)
    uploaded: list[dict[str, Any]] = field(default_factory=list)

    def get(self, name: str, version: str) -> Any:
        key = (name, version)
        if key not in self.existing:
            raise _NotFound(key)
        return self.existing[key]

    def upload_file(self, *, name: str, version: str, file_path: str) -> Any:
        self.uploaded.append({"name": name, "version": version, "file_path": file_path})
        # Simulate the SDK reading the file — verify the temp file exists.
        assert Path(file_path).exists(), "upload_file called after temp file deletion"
        asset = _FakeDataset(
            id=f"azureai://accounts/fake-acct/projects/fake-proj/data/{name}/versions/{version}"
        )
        self.existing[(name, version)] = asset
        return asset


@dataclass
class _FakeEvalObject:
    id: str
    name: str
    testing_criteria: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class _FakeEvalRun:
    id: str


@dataclass
class _FakeEvals:
    existing: list[_FakeEvalObject] = field(default_factory=list)
    created_evals: list[dict[str, Any]] = field(default_factory=list)
    _run_counter: int = 0
    runs: "_FakeEvalRuns | None" = None

    def __post_init__(self) -> None:
        self.runs = _FakeEvalRuns(parent=self)

    def list(self, **_: Any) -> Iterable[_FakeEvalObject]:
        return iter(self.existing)

    def create(self, *, name: str, data_source_config: dict, testing_criteria: list, metadata: dict = None) -> _FakeEvalObject:  # type: ignore[assignment]
        eval_id = f"eval_fake_{len(self.created_evals) + 1}"
        obj = _FakeEvalObject(
            id=eval_id, name=name, testing_criteria=list(testing_criteria)
        )
        self.existing.append(obj)
        self.created_evals.append(
            {
                "name": name,
                "data_source_config": data_source_config,
                "testing_criteria": testing_criteria,
                "metadata": metadata,
            }
        )
        return obj


@dataclass
class _FakeEvalRuns:
    parent: _FakeEvals
    created_runs: list[dict[str, Any]] = field(default_factory=list)

    def create(self, eval_id: str, *, data_source: dict, name: str, metadata: dict = None) -> _FakeEvalRun:  # type: ignore[assignment]
        self.parent._run_counter += 1
        run_id = f"evalrun_fake_{self.parent._run_counter}"
        self.created_runs.append(
            {
                "eval_id": eval_id,
                "data_source": data_source,
                "name": name,
                "metadata": metadata,
            }
        )
        return _FakeEvalRun(id=run_id)


@dataclass
class _FakeOpenAIClient:
    evals: _FakeEvals = field(default_factory=_FakeEvals)


@dataclass
class _FakeBeta:
    evaluators: _FakeEvaluators


@dataclass
class _FakeProjectClient:
    beta: _FakeBeta
    datasets: _FakeDatasets
    _openai: _FakeOpenAIClient

    def get_openai_client(self) -> _FakeOpenAIClient:
        return self._openai


def _fake_client(
    *,
    existing_evaluators: dict[tuple[str, str], Any] | None = None,
    existing_datasets: dict[tuple[str, str], Any] | None = None,
    existing_evals: list[_FakeEvalObject] | None = None,
) -> _FakeProjectClient:
    return _FakeProjectClient(
        beta=_FakeBeta(evaluators=_FakeEvaluators(existing=existing_evaluators or {})),
        datasets=_FakeDatasets(existing=existing_datasets or {}),
        _openai=_FakeOpenAIClient(evals=_FakeEvals(existing=existing_evals or [])),
    )


# ── Naming helpers ──────────────────────────────────────────────────


def test_default_eval_name_uses_suite_id_prefix() -> None:
    assert default_eval_name(_make_run()) == "ASSERT: helpful-qa"
    assert EVAL_NAME_PREFIX == "ASSERT: "


def test_default_run_name_uses_run_id_prefix() -> None:
    assert default_run_name(_make_run()) == "ASSERT run: run-1"
    assert RUN_NAME_PREFIX == "ASSERT run: "


def test_default_dataset_name_uses_suite_id_prefix() -> None:
    assert default_dataset_name(_make_run()) == "assert-helpful-qa"
    assert DATASET_NAME_PREFIX == "assert-"


def test_default_dataset_name_sanitizes_character_class() -> None:
    """Foundry accepts only [a-z0-9_-]. Non-matching chars get collapsed to '-'."""
    run = _make_run(suite_id="Foo Bar/Baz.qux")

    result = default_dataset_name(run)

    # All lowercase, no spaces, no slashes, no dots.
    assert result == "assert-foo-bar-baz-qux"


# ── strip_litellm_prefix ────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("azure/gpt-5.4-mini", "gpt-5.4-mini"),
        ("openai/gpt-4o", "gpt-4o"),
        ("bedrock/claude-3", "claude-3"),
        ("gpt-5.4-mini", "gpt-5.4-mini"),
        ("", ""),
    ],
)
def test_strip_litellm_prefix(raw: str, expected: str) -> None:
    assert strip_litellm_prefix(raw) == expected


# ── resolve_judge_deployment ────────────────────────────────────────


def test_resolve_judge_deployment_prefers_pipeline_judge_model() -> None:
    config = {
        "default_model": {"name": "azure/default"},
        "pipeline": {"judge": {"model": {"name": "azure/judge-override"}}},
    }

    assert resolve_judge_deployment(_make_run(config=config)) == "judge-override"


def test_resolve_judge_deployment_falls_back_to_default_model() -> None:
    config = {"default_model": {"name": "azure/default"}}

    assert resolve_judge_deployment(_make_run(config=config)) == "default"


def test_resolve_judge_deployment_falls_back_to_scores_row() -> None:
    scores = [
        {
            "test_case_id": "tc-1",
            "judge_model": "openai/from-scores",
            "judge_status": "ok",
            "verdict": {"dimensions": {"policy_violation": False}},
        }
    ]

    assert resolve_judge_deployment(_make_run(config={}, scores=scores)) == "from-scores"


def test_resolve_judge_deployment_returns_empty_when_nothing_configured() -> None:
    scores = [
        {
            "test_case_id": "tc-1",
            "judge_status": "ok",
            "verdict": {"dimensions": {"policy_violation": False}},
        }
    ]

    assert resolve_judge_deployment(_make_run(config={}, scores=scores)) == ""


# ── Dry-run ─────────────────────────────────────────────────────────


def test_dry_run_returns_dry_run_result() -> None:
    result = push_run(_make_run(), dry_run=True)

    assert isinstance(result, DryRunResult)
    assert result.eval_name == "ASSERT: helpful-qa"
    assert result.run_name == "ASSERT run: run-1"
    assert result.dataset_name == "assert-helpful-qa"
    assert result.dataset_row_count == 1
    assert result.judge_deployment == "gpt-5.4"
    assert len(result.dataset_version) == 12


def test_dry_run_emits_specs_for_both_variants_by_default() -> None:
    result = push_run(_make_run(), dry_run=True)

    assert isinstance(result, DryRunResult)
    variants = sorted({s.variant for s in result.evaluator_specs})
    assert variants == ["code", "prompt"]
    names = sorted(s.evaluator_name for s in result.evaluator_specs)
    assert names == [
        "assert-overrefusal",
        "assert-overrefusal-rescore",
        "assert-policy_violation",
        "assert-policy_violation-rescore",
    ]


def test_dry_run_code_mode_only_emits_code_specs() -> None:
    result = push_run(_make_run(), evaluator_mode="code", dry_run=True)

    assert isinstance(result, DryRunResult)
    assert {s.variant for s in result.evaluator_specs} == {"code"}


def test_dry_run_makes_no_network_calls() -> None:
    """Dry-run must not construct or touch a project client."""
    # No project_client, no project — dry-run must not need either.
    result = push_run(_make_run(), dry_run=True)

    assert isinstance(result, DryRunResult)


def test_dry_run_carries_passing_when_true_overrides() -> None:
    result = push_run(
        _make_run(),
        passing_when_true={"answer_quality": True},
        dry_run=True,
    )

    assert isinstance(result, DryRunResult)
    assert result.passing_when_true == {"answer_quality": True}


def test_dry_run_exposes_evaluator_fingerprints() -> None:
    """Dry-run surfaces the same fingerprint the drift check would compare against.

    Lets a caller detect config-level changes that would trigger a
    Foundry-side delete+recreate (rubric prose edited in
    config.yaml, etc.) without hitting the network.
    """
    result = push_run(_make_run(), dry_run=True)

    assert isinstance(result, DryRunResult)
    assert set(result.evaluator_fingerprints) == {
        "assert-overrefusal",
        "assert-overrefusal-rescore",
        "assert-policy_violation",
        "assert-policy_violation-rescore",
    }
    for name, fp in result.evaluator_fingerprints.items():
        assert isinstance(fp, str) and len(fp) == 12, name


def test_dry_run_exposes_prompt_variant_call_estimate() -> None:
    """LLM cost estimate = (# prompt evaluators) × (# dataset rows).

    Surfaces the Foundry-side inference cost of a prompt-variant push
    without any network calls. Code-only mode reports 0.
    """
    result = push_run(_make_run(), dry_run=True)  # default mode is 'both'
    assert isinstance(result, DryRunResult)
    # 2 dims × 1 row × 1 prompt evaluator per dim = 2 calls.
    assert result.prompt_variant_calls == 2

    code_only = push_run(_make_run(), evaluator_mode="code", dry_run=True)
    assert isinstance(code_only, DryRunResult)
    assert code_only.prompt_variant_calls == 0

    prompt_only = push_run(_make_run(), evaluator_mode="prompt", dry_run=True)
    assert isinstance(prompt_only, DryRunResult)
    # 2 dims × 1 row = 2 calls.
    assert prompt_only.prompt_variant_calls == 2


def test_dry_run_fingerprint_flips_when_rubric_prose_changes() -> None:
    """Editing rubric prose in config.yaml must flip the prompt-variant fingerprint.

    Verifies the exact E2E signal customers rely on: change something
    in the run config → the fingerprint for the affected prompt
    evaluator differs → the next real push triggers delete+recreate.
    """
    base_config = {
        "default_model": {"name": "azure/gpt-5.4"},
        "pipeline": {
            "judge": {
                "dimensions": {
                    "policy_violation": {
                        "description": "Original rubric prose",
                        "rubric": "1=violation, 5=clean",
                    }
                }
            }
        },
    }
    edited_config = {
        "default_model": {"name": "azure/gpt-5.4"},
        "pipeline": {
            "judge": {
                "dimensions": {
                    "policy_violation": {
                        "description": "REVISED rubric prose",
                        "rubric": "1=violation, 5=clean",
                    }
                }
            }
        },
    }

    before = push_run(_make_run(config=base_config), dry_run=True)
    after = push_run(_make_run(config=edited_config), dry_run=True)

    assert isinstance(before, DryRunResult) and isinstance(after, DryRunResult)
    # Prompt variant reads rubric prose ⇒ fingerprint flips.
    assert (
        before.evaluator_fingerprints["assert-policy_violation-rescore"]
        != after.evaluator_fingerprints["assert-policy_violation-rescore"]
    )
    # Code variant is hard-coded ⇒ fingerprint stays stable across rubric edits.
    assert (
        before.evaluator_fingerprints["assert-policy_violation"]
        == after.evaluator_fingerprints["assert-policy_violation"]
    )


# ── push_run — orchestration ────────────────────────────────────────


def test_push_ends_up_with_eval_and_run_ids() -> None:
    client = _fake_client()

    result = push_run(_make_run(), project_client=client)

    assert isinstance(result, PushResult)
    assert result.eval_id.startswith("eval_fake_")
    assert result.run_id.startswith("evalrun_fake_")


def test_push_registers_evaluators_when_missing() -> None:
    client = _fake_client()

    push_run(_make_run(), project_client=client)

    created_names = sorted(name for name, _ in client.beta.evaluators.created)
    assert created_names == [
        "assert-overrefusal",
        "assert-overrefusal-rescore",
        "assert-policy_violation",
        "assert-policy_violation-rescore",
    ]


def _existing_evaluator(definition_type: str, *, version: str = "1") -> dict[str, Any]:
    """A minimal SDK-shaped evaluator version stub with the requested type.

    Deliberately sparse — everything else is missing so this stub's
    fingerprint will never match a real spec's fingerprint. Used for
    stale-shape drift tests where the whole point is that the
    registered version fingerprints differently from what the
    pipeline is about to send.
    """
    return {"definition": {"type": definition_type}, "version": version}


def _existing_evaluators_matching_run(run: AssertRun) -> dict[tuple[str, str], Any]:
    """Seed pre-existing evaluators whose fingerprints match what the pipeline would build.

    Runs the same spec builder the pipeline uses so the stored
    ``evaluator_version`` fingerprints byte-identical to the freshly
    built one. That's the setup the "reuses existing" test needs.
    Each stored evaluator carries an explicit ``version`` attribute
    so the version-bump path in :func:`_ensure_evaluators` can read
    it back through ``list_versions``.
    """
    from assert_ai.integrations.foundry.evaluators import build_evaluator_specs_for_run

    result: dict[tuple[str, str], Any] = {}
    for spec in build_evaluator_specs_for_run(run, mode="both"):
        # Real SDK models expose ``.version``; stamp it explicitly
        # so the fake's list_versions returns something the pipeline
        # can recognize as the matching version.
        try:
            setattr(spec.evaluator_version, "version", "1")
        except (AttributeError, TypeError):
            pass
        result[(spec.evaluator_name, "1")] = spec.evaluator_version
    return result


def test_push_reuses_existing_evaluators() -> None:
    """A pre-existing version whose fingerprint matches is reused; no new version registered."""
    run = _make_run()
    client = _fake_client(existing_evaluators=_existing_evaluators_matching_run(run))

    result = push_run(run, project_client=client)

    assert client.beta.evaluators.created == []  # nothing re-registered
    assert client.beta.evaluators.deleted == []  # nothing dropped either
    assert isinstance(result, PushResult)
    assert sorted(result.reused_evaluators) == [
        "assert-overrefusal",
        "assert-overrefusal-rescore",
        "assert-policy_violation",
        "assert-policy_violation-rescore",
    ]
    # Reused refs carry the version that matched.
    for ref in result.evaluator_refs:
        assert ref.evaluator_version == "1"


def test_push_bumps_version_for_stale_rubric_evaluator() -> None:
    """Existing 'rubric' (v1 shape) fingerprint mismatch ⇒ register at v2, keep v1 intact.

    v1 ASSERT registered ``assert-{dim}`` as ``type: rubric``. The v2
    exporter fingerprints the current spec, sees the mismatch, and
    registers a NEW version rather than deleting and overwriting v1.
    Old eval runs pinned to v1 continue to render their original
    grader body in the Foundry UI.
    """
    stale = {("assert-policy_violation", "1"): _existing_evaluator("rubric", version="1")}
    client = _fake_client(existing_evaluators=stale)

    result = push_run(_make_run(), evaluator_mode="code", project_client=client)

    assert isinstance(result, PushResult)
    # v1 is NOT deleted.
    assert client.beta.evaluators.deleted == []
    # v2 was registered for policy_violation.
    created_names = {name for name, _ in client.beta.evaluators.created}
    assert "assert-policy_violation" in created_names
    pv_ref = next(ref for ref in result.evaluator_refs if ref.evaluator_name == "assert-policy_violation")
    assert pv_ref.evaluator_version == "2"
    # Not counted as reused because a new version was registered.
    assert "assert-policy_violation" not in result.reused_evaluators


def test_push_bumps_version_when_code_text_drifts() -> None:
    """Same type, different grader body ⇒ fingerprint mismatch ⇒ register at v2.

    Mutates the ``code_text`` of a pre-existing code evaluator so it
    fingerprints differently from the fresh spec. The pipeline must
    detect the drift and register the new spec at ``max(existing) + 1``,
    leaving v1 intact.
    """
    stale_spec = build_code_evaluator_spec("policy_violation", description="whatever")
    stale_version = stale_spec.evaluator_version
    # Same type + schema, but a manually-mutated grader body.
    stale_version.definition.code_text = "def grade(sample, item):\n    return 0.5\n"
    setattr(stale_version, "version", "1")
    stale = {("assert-policy_violation", "1"): stale_version}
    client = _fake_client(existing_evaluators=stale)

    result = push_run(_make_run(), evaluator_mode="code", project_client=client)

    assert isinstance(result, PushResult)
    # v1 stays.
    assert client.beta.evaluators.deleted == []
    # v2 registered.
    assert "assert-policy_violation" in {name for name, _ in client.beta.evaluators.created}
    ref = next(r for r in result.evaluator_refs if r.evaluator_name == "assert-policy_violation")
    assert ref.evaluator_version == "2"
    assert "assert-policy_violation" not in result.reused_evaluators


def test_push_bumps_version_when_prompt_text_drifts() -> None:
    """Same type, different rubric prose in ``prompt_text`` ⇒ register at v2.

    Simulates a customer editing their ASSERT config's rubric between
    pushes: the resulting prompt-variant evaluator's ``prompt_text``
    changes, its fingerprint differs, and the pipeline registers a
    new version so new eval runs (that reference the new version) pick
    up the new rubric while historical runs still see the old one.
    """
    stale_spec = build_prompt_evaluator_spec(
        "policy_violation", description="whatever", rubric_prose="OLD RUBRIC PROSE"
    )
    stale_version = stale_spec.evaluator_version
    stale_version.definition.prompt_text = "MUTATED PROMPT TEMPLATE"
    setattr(stale_version, "version", "1")
    stale = {("assert-policy_violation-rescore", "1"): stale_version}
    client = _fake_client(existing_evaluators=stale)

    result = push_run(_make_run(), evaluator_mode="prompt", project_client=client)

    assert isinstance(result, PushResult)
    assert client.beta.evaluators.deleted == []
    assert "assert-policy_violation-rescore" in {name for name, _ in client.beta.evaluators.created}
    ref = next(r for r in result.evaluator_refs if r.evaluator_name == "assert-policy_violation-rescore")
    assert ref.evaluator_version == "2"
    assert "assert-policy_violation-rescore" not in result.reused_evaluators


def test_push_reuses_matching_prior_version_even_when_not_latest() -> None:
    """A prior version with the SAME fingerprint is reused, even if a newer version exists.

    Guarantees that pushing the same run repeatedly doesn't churn
    versions: v1 = fingerprint A, v2 = fingerprint B (a customer's
    experiment), pushing fingerprint A again reuses v1 rather than
    registering v3.
    """
    matching_spec = build_code_evaluator_spec("policy_violation", description="whatever")
    matching_version = matching_spec.evaluator_version
    setattr(matching_version, "version", "1")

    # An unrelated newer version that fingerprints differently.
    diverged_spec = build_code_evaluator_spec("policy_violation", description="whatever")
    diverged_version = diverged_spec.evaluator_version
    diverged_version.definition.code_text = "def grade(sample, item):\n    return 0.5\n"
    setattr(diverged_version, "version", "2")

    existing = {
        ("assert-policy_violation", "1"): matching_version,
        ("assert-policy_violation", "2"): diverged_version,
    }
    client = _fake_client(existing_evaluators=existing)

    result = push_run(_make_run(), evaluator_mode="code", project_client=client)

    assert isinstance(result, PushResult)
    # policy_violation is reused at v1; other dims (overrefusal here) may be
    # registered new because we didn't seed them. Assert only on the target.
    created_names = {name for name, _ in client.beta.evaluators.created}
    assert "assert-policy_violation" not in created_names
    ref = next(r for r in result.evaluator_refs if r.evaluator_name == "assert-policy_violation")
    assert ref.evaluator_version == "1"
    assert "assert-policy_violation" in result.reused_evaluators


def test_push_ignores_description_only_drift() -> None:
    """Same grader body, different description prose ⇒ reuse, no new version.

    The description is UI-only and doesn't affect scoring behavior.
    A customer editing prose in their config should not churn
    evaluator versions on every push.
    """
    fresh_spec = build_code_evaluator_spec("policy_violation", description="whatever")
    stored = fresh_spec.evaluator_version
    stored.description = "Different prose that customers might edit repeatedly"
    setattr(stored, "version", "1")
    stale = {("assert-policy_violation", "1"): stored}
    client = _fake_client(existing_evaluators=stale)

    result = push_run(_make_run(), evaluator_mode="code", project_client=client)

    assert isinstance(result, PushResult)
    assert client.beta.evaluators.deleted == []
    # No re-registration because the fingerprint matched.
    assert not any(name == "assert-policy_violation" for name, _ in client.beta.evaluators.created)
    assert "assert-policy_violation" in result.reused_evaluators


def test_push_propagates_non_typeerror_from_list_versions() -> None:
    """SDK errors from ``list_versions`` must bubble up, not be swallowed.

    Regression guard for the fallback catch in
    :func:`_list_evaluator_versions`. A transient SDK exception
    (auth expiry, throttling, network) must NOT fall through to the
    enumerate path — that would silently treat the name as fresh
    and register a duplicate on top of the existing catalog. Only
    kwarg-mismatch ``TypeError`` (from older-signature fakes) is
    tolerated.
    """

    class _Boom(RuntimeError):
        pass

    class _RaisingEvaluators:
        def list_versions(self, name: str, **_: Any) -> list[Any]:
            raise _Boom("simulated SDK failure")

        def get_version(self, name: str, version: str) -> Any:  # pragma: no cover
            raise AssertionError("must not fall through to enumerate path")

        def create_version(self, name: str, evaluator_version: Any) -> Any:  # pragma: no cover
            raise AssertionError("must not register on top of an unknown catalog")

    client = _fake_client()
    client.beta.evaluators = _RaisingEvaluators()

    with pytest.raises(_Boom):
        push_run(_make_run(), evaluator_mode="code", project_client=client)


# ── Fingerprint round-trip normalization ────────────────────────────


def test_fingerprint_matches_across_int_float_metric_bounds() -> None:
    """Foundry echoes ``min_value=1`` back as ``1.0``. Fingerprint must ignore that.

    The ordinal metric on the prompt variant is registered with
    integer bounds, but Foundry normalizes them to floats on the
    wire. If the fingerprint hashed ints and floats differently,
    every subsequent push would register a new version even though
    the grader body is unchanged.
    """
    spec = build_prompt_evaluator_spec(
        "policy_violation", description="x", rubric_prose="Score 1-5."
    )
    sent = spec.evaluator_version
    sent_fp = _definition_fingerprint(sent)

    # Now imagine Foundry echoed the same evaluator back with float bounds.
    fetched = build_prompt_evaluator_spec(
        "policy_violation", description="x", rubric_prose="Score 1-5."
    ).evaluator_version
    fetched.definition.metrics["result"].min_value = 1.0
    fetched.definition.metrics["result"].max_value = 5.0
    fetched_fp = _definition_fingerprint(fetched)

    assert sent_fp == fetched_fp


def test_fingerprint_matches_when_opposite_body_field_is_empty_string() -> None:
    """Foundry echoes ``prompt_text=None`` back as ``prompt_text=""`` on code evaluators.

    Symmetrically, ``code_text=None`` on a prompt evaluator can
    come back as ``""``. The fingerprint must treat the two as
    equivalent, otherwise every push after the first would register
    a new version.
    """
    fresh = build_code_evaluator_spec("policy_violation", description="x").evaluator_version
    fresh_fp = _definition_fingerprint(fresh)

    # Simulate what Foundry returns: prompt_text normalized to "".
    echoed = build_code_evaluator_spec("policy_violation", description="x").evaluator_version
    echoed.definition.prompt_text = ""
    echoed_fp = _definition_fingerprint(echoed)

    assert fresh_fp == echoed_fp


def test_push_uploads_dataset_when_content_hash_new() -> None:
    client = _fake_client()

    result = push_run(_make_run(), project_client=client)

    assert isinstance(result, PushResult)
    assert result.reused_dataset is False
    assert len(client.datasets.uploaded) == 1
    upload = client.datasets.uploaded[0]
    assert upload["name"] == "assert-helpful-qa"
    assert len(upload["version"]) == 12  # content hash length


def test_push_reuses_dataset_when_content_hash_matches() -> None:
    """Second push with identical content ⇒ dataset reuse, no upload."""
    client_a = _fake_client()
    result_a = push_run(_make_run(), project_client=client_a)
    assert isinstance(result_a, PushResult)
    uploaded_version = client_a.datasets.uploaded[0]["version"]

    # Second push against the SAME client (pre-existing dataset at same version).
    client_b = _fake_client(
        existing_datasets={
            ("assert-helpful-qa", uploaded_version): _FakeDataset(
                id=f"azureai://.../data/assert-helpful-qa/versions/{uploaded_version}"
            )
        }
    )
    result_b = push_run(_make_run(), project_client=client_b)

    assert isinstance(result_b, PushResult)
    assert result_b.reused_dataset is True
    assert client_b.datasets.uploaded == []


def test_push_reuses_eval_when_name_matches_and_criteria_match() -> None:
    """Two pushes of the same run ⇒ one eval, many runs under it."""
    client_a = _fake_client()
    result_a = push_run(_make_run(), project_client=client_a)
    assert isinstance(result_a, PushResult)
    created_criteria = client_a._openai.evals.created_evals[0]["testing_criteria"]

    # Second push against a client that already has that eval.
    existing_eval = _FakeEvalObject(
        id="eval_prior",
        name="ASSERT: helpful-qa",
        testing_criteria=list(created_criteria),
    )
    client_b = _fake_client(existing_evals=[existing_eval])

    result_b = push_run(_make_run(), project_client=client_b)

    assert isinstance(result_b, PushResult)
    assert result_b.reused_eval is True
    assert result_b.eval_id == "eval_prior"
    # No new eval created; only a new run.
    assert client_b._openai.evals.created_evals == []
    assert len(client_b._openai.evals.runs.created_runs) == 1


def test_push_fails_loudly_on_testing_criteria_drift() -> None:
    """Eval exists with different criteria ⇒ raise (Foundry can't PATCH criteria)."""
    existing_eval = _FakeEvalObject(
        id="eval_prior",
        name="ASSERT: helpful-qa",
        testing_criteria=[{"evaluator_name": "some-other-evaluator"}],
    )
    client = _fake_client(existing_evals=[existing_eval])

    with pytest.raises(PushError, match="different testing_criteria"):
        push_run(_make_run(), project_client=client)


def test_push_run_metadata_carries_ids_and_hashes() -> None:
    client = _fake_client()

    push_run(_make_run(), project_client=client)

    run_meta = client._openai.evals.runs.created_runs[0]["metadata"]
    assert run_meta["assert.run_id"] == "run-1"
    assert run_meta["assert.inference_config_hash"] == "abc123"
    assert run_meta["assert.judge_config_hash"] == "def456"
    assert "assert.dataset_version" in run_meta


def test_push_eval_metadata_carries_suite_context() -> None:
    client = _fake_client()

    push_run(_make_run(), project_client=client)

    eval_meta = client._openai.evals.created_evals[0]["metadata"]
    assert eval_meta["assert.source"] == "assert-ai"
    assert eval_meta["assert.suite_id"] == "helpful-qa"
    assert eval_meta["assert.row_count"] == "1"


def test_bounded_record_truncates_on_utf8_byte_boundary() -> None:
    """Metadata values must fit under Foundry's per-value byte cap (512)."""
    from assert_ai.integrations.foundry.pipeline import _bounded_record

    # ASCII: 600 chars = 600 bytes ⇒ trimmed to 512 bytes total including "…".
    ascii_600 = "x" * 600
    # Multi-byte: 300 chars of a 3-byte code point = 900 bytes.
    multibyte_300 = "\u4e2d" * 300  # "中" is 3 bytes in UTF-8.

    result = _bounded_record(
        [
            ("short", "ok"),
            ("ascii", ascii_600),
            ("multi", multibyte_300),
        ]
    )

    assert result["short"] == "ok"
    for key in ("ascii", "multi"):
        value = result[key]
        assert value.endswith("…"), (key, value[-5:])
        assert len(value.encode("utf-8")) <= 512, (key, len(value.encode("utf-8")))


def test_push_run_data_source_wires_dataset_asset_id() -> None:
    client = _fake_client()

    push_run(_make_run(), project_client=client)

    created = client._openai.evals.runs.created_runs[0]
    assert created["data_source"]["type"] == "jsonl"
    assert created["data_source"]["source"]["type"] == "file_id"
    # The dataset asset id came from the fake dataset upload.
    assert "assert-helpful-qa" in created["data_source"]["source"]["id"]


def test_push_testing_criteria_wire_deployment_for_prompt_variants() -> None:
    client = _fake_client()

    push_run(_make_run(), project_client=client)

    criteria = client._openai.evals.created_evals[0]["testing_criteria"]
    prompt_criteria = [c for c in criteria if c["evaluator_name"].endswith("-rescore")]
    for c in prompt_criteria:
        assert c["initialization_parameters"]["deployment_name"] == "gpt-5.4"
        assert c["initialization_parameters"]["threshold"] == 3.0


def test_push_testing_criteria_code_variants_have_empty_init_parameters() -> None:
    client = _fake_client()

    push_run(_make_run(), evaluator_mode="code", project_client=client)

    criteria = client._openai.evals.created_evals[0]["testing_criteria"]
    for c in criteria:
        assert c["initialization_parameters"] == {}


def test_push_data_source_config_tolerates_missing_scores() -> None:
    """The eval's item_schema must accept rows with an empty ``assert_scores`` map.

    :func:`build_dataset_rows` emits one row per inference entry — even
    entries whose judge errored (empty ``assert_scores``). If the
    data_source_config declared any dimension as ``required``, Foundry
    would reject those rows at dataset validation. The un-scored
    conversation should still show up in the Foundry UI.
    """
    client = _fake_client()

    push_run(_make_run(), project_client=client)

    dsc = client._openai.evals.created_evals[0]["data_source_config"]
    item = dsc["item_schema"]
    assert item["required"] == ["query", "response"]
    assert item["properties"]["assert_scores"]["required"] == []
    assert item["properties"]["assert_reasons"]["required"] == []


# ── Error paths ─────────────────────────────────────────────────────


def test_push_without_client_or_project_raises() -> None:
    """No SDK client and no project = nothing to push against."""
    with pytest.raises(PushError, match="project"):
        push_run(_make_run())


def test_push_prompt_mode_without_judge_model_raises() -> None:
    """Prompt variant needs a deployment name at eval-create time."""
    run = _make_run(
        config={},  # no default_model, no pipeline.judge.model
        scores=[
            {
                "test_case_id": "tc-1",
                "judge_status": "ok",
                "verdict": {"dimensions": {"policy_violation": False}},
            }
        ],
    )

    with pytest.raises(PushError, match="judge model name"):
        push_run(run, evaluator_mode="prompt", project_client=_fake_client())


def test_push_code_mode_without_judge_model_ok() -> None:
    """Code variant doesn't call any model, so no deployment needed."""
    run = _make_run(
        config={},
        scores=[
            {
                "test_case_id": "tc-1",
                "judge_status": "ok",
                "verdict": {"dimensions": {"policy_violation": False}},
            }
        ],
    )

    # Should not raise.
    result = push_run(run, evaluator_mode="code", project_client=_fake_client())

    assert isinstance(result, PushResult)


def test_push_no_scored_dimensions_raises() -> None:
    scores = [{"test_case_id": "tc-1", "judge_status": "ok", "verdict": {}}]

    with pytest.raises(PushError, match="no scored dimensions"):
        push_run(_make_run(scores=scores), project_client=_fake_client())


# ── push_run_dir (thin loader wrapper) ──────────────────────────────


def test_push_run_dir_loads_from_disk_and_delegates() -> None:
    """Smoke test the thin dir-loading wrapper against the real fixture."""
    result = push_run_dir(
        "artifacts/results/foundry-agent-smoke/smoke-1",
        dry_run=True,
    )

    assert isinstance(result, DryRunResult)
    assert result.eval_name == "ASSERT: foundry-agent-smoke"
    assert result.dataset_row_count == 3


# ── Endpoint resolution ─────────────────────────────────────────────


def test_endpoint_resolution_accepts_url() -> None:
    """Full endpoint URL passes through untouched (except trailing slash)."""
    from assert_ai.integrations.foundry.pipeline import _endpoint_from_project

    url = "https://foo.services.ai.azure.com/api/projects/bar"
    assert _endpoint_from_project(url) == url
    assert _endpoint_from_project(url + "/") == url


def test_endpoint_resolution_accepts_shorthand() -> None:
    from assert_ai.integrations.foundry.pipeline import _endpoint_from_project

    assert (
        _endpoint_from_project("foo/bar")
        == "https://foo.services.ai.azure.com/api/projects/bar"
    )


def test_endpoint_resolution_accepts_arm_id() -> None:
    from assert_ai.integrations.foundry.pipeline import _endpoint_from_project

    arm = (
        "/subscriptions/xxx/resourceGroups/rg/providers/"
        "Microsoft.CognitiveServices/accounts/acct/projects/proj"
    )
    assert (
        _endpoint_from_project(arm)
        == "https://acct.services.ai.azure.com/api/projects/proj"
    )


def test_endpoint_resolution_rejects_junk() -> None:
    from assert_ai.integrations.foundry.pipeline import _endpoint_from_project

    with pytest.raises(PushError, match="Cannot resolve Foundry endpoint"):
        _endpoint_from_project("not-a-project")


# ── Lazy load via package root ──────────────────────────────────────


def test_lazy_load_via_package_root() -> None:
    import assert_ai.integrations.foundry as foundry

    assert foundry.push_run is push_run
    assert foundry.push_run_dir is push_run_dir
    assert foundry.PushError is PushError
    assert foundry.PushResult is PushResult
    assert foundry.DryRunResult is DryRunResult
    assert foundry.EvaluatorRef is EvaluatorRef
    assert foundry.DatasetRef is DatasetRef
    assert foundry.default_eval_name is default_eval_name
    assert foundry.default_run_name is default_run_name
    assert foundry.default_dataset_name is default_dataset_name
    assert foundry.resolve_judge_deployment is resolve_judge_deployment
    assert foundry.strip_litellm_prefix is strip_litellm_prefix
