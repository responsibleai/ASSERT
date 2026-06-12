# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Shared display labels for CLI output.

Issue #58: align CLI rendering with the SvelteKit viewer so that a user reading
either surface sees the same field names, status labels, and stage names. The
viewer is the source of truth; this module mirrors its label maps in Python.

Viewer references (do not drift):
- Status labels: ``viewer/src/routes/+page.svelte`` (statusConfig)
- Stage labels: ``viewer/src/routes/suite/[suite_id]/[run_id]/monitor/+page.svelte``
- Metric labels: ``viewer/src/lib/ResultDrawer.svelte`` (metricLabel)
"""

from __future__ import annotations


# Maps suite-status keys to the human label the viewer shows for the same
# suite in its home grid. Keep in lockstep with ``statusConfig`` in
# ``viewer/src/routes/+page.svelte``.
STATUS_LABELS: dict[str, str] = {
    "systematized": "Behavior Categories Defined",
    "test_set_ready": "Evaluation Test Set Generated",
    "has_results": "Has Evaluation Result",
}


# Maps overall run manifest status values to the human label the viewer shows
# for run badges and headers. Keep separate from per-stage statuses: a failed
# run is "Failed", while a failed stage is normalized to "Error".
RUN_STATUS_LABELS: dict[str, str] = {
    "running": "Running",
    "completed": "Completed",
    "failed": "Failed",
    "abandoned": "Abandoned",
    "unknown": "Unknown",
}


# Maps runner stage names (``assert_ai/stages/__init__.py``) to the human
# label the viewer's run monitor uses. ``systematize`` and ``taxonomy`` resolve
# to the same label because the viewer historically used the artifact name.
# Keep the sub-stage entries (``systematization``,
# ``systematization_convert``) in sync with ``stageLabels`` in
# ``viewer/src/routes/suite/[suite_id]/[run_id]/monitor/+page.svelte``; those
# can appear in ``manifest.stages`` for runs that broke ``systematize`` into
# its two phases, and without them the CLI would drift to snake-case while the
# viewer rendered the locked label.
STAGE_LABELS: dict[str, str] = {
    "systematize": "Behavior Categories Generation",
    "taxonomy": "Behavior Categories Generation",
    "test_set": "Test Set Generation",
    "inference": "Inference",
    "judge": "Scoring",
    "systematization": "Systematization",
    "systematization_convert": "Behavior Categories Conversion",
}


# Maps run-stage execution status to the human label the viewer shows for the
# same status. The viewer normalizes raw manifest ``failed`` per-stage values
# to ``error`` before rendering (see ``toUiStageStatus`` in
# ``viewer/src/lib/server/run-status.ts``) and ``formatStageStatus`` in the
# monitor page then renders that ``error`` as ``"Error"``. Collapse both keys
# to ``"Error"`` here so a failed stage reads the same way on the CLI.
STAGE_STATUS_LABELS: dict[str, str] = {
    "pending": "Pending",
    "running": "Running",
    "completed": "Complete",
    "skipped": "Skipped",
    "error": "Error",
    "failed": "Error",
}


# Compound metric labels we want to lock in (anything not in here falls back to
# ``snake_to_sentence``). The viewer's ``metricLabel`` is just snake-to-space +
# capitalize-first, so this map only needs entries where we want a tighter
# phrasing than the default produces.
METRIC_LABELS: dict[str, str] = {
    "policy_violation": "Policy violation",
    "policy_violation_rate": "Policy violation rate",
    "overrefusal": "Overrefusal",
    "overrefusal_rate": "Overrefusal rate",
    "harm_actionability": "Harm actionability",
    "judge_failure": "Judge failure",
    "judge_failure_rate": "Judge failure rate",
    "judge_failures": "Judge failures",
}


def _snake_to_sentence(value: str) -> str:
    spaced = value.replace("_", " ").strip()
    if not spaced:
        return spaced
    return spaced[0].upper() + spaced[1:]


def label_status(status: str | None) -> str:
    """Return the viewer-aligned label for a suite status key."""
    if not status:
        return "—"
    return STATUS_LABELS.get(status, _snake_to_sentence(status))


def label_run_status(status: str | None) -> str:
    """Return the viewer-aligned label for an overall run manifest status."""
    if not status:
        return "—"
    return RUN_STATUS_LABELS.get(status, _snake_to_sentence(status))


def label_stage(stage: str | None) -> str:
    """Return the viewer-aligned label for a runner stage name."""
    if not stage or stage in {"-", "—"}:
        return "—"
    return STAGE_LABELS.get(stage, _snake_to_sentence(stage))


def label_stage_status(status: str | None) -> str:
    """Return the viewer-aligned label for a stage execution status."""
    if not status:
        return "—"
    return STAGE_STATUS_LABELS.get(status, _snake_to_sentence(status))


def label_metric(metric: str | None) -> str:
    """Return the viewer-aligned label for a metric key.

    Matches ``metricLabel`` in ``viewer/src/lib/ResultDrawer.svelte`` for any
    metric the viewer would also encounter: snake-to-space, capitalize the
    first character. Compound names registered in :data:`METRIC_LABELS` win.
    """
    if not metric:
        return "—"
    if metric in METRIC_LABELS:
        return METRIC_LABELS[metric]
    return _snake_to_sentence(metric)
