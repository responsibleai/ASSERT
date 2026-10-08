# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Import an OTLP cohort and score it through the existing judge-only pipeline."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path

import yaml

from assert_ai.config import load_config, load_runtime_context, resolve_stage_paths
from assert_ai.core.io import load_jsonl, write_json, write_jsonl
from assert_ai.core.judge import build_judge_contract, infer_judge_status
from assert_ai.core.otel import parse_otel_traces
from assert_ai.core.transcript import TranscriptEvent
from assert_ai.runner import run_pipeline
from assert_ai.stages import STAGES
from assert_ai.stages.judge import JUDGE_SYSTEM_PROMPT


def judge_trace_file(
    *,
    traces: Path,
    config: Path,
    group_by: str = "session.id",
    output: Path | None = None,
) -> tuple[int, Path, dict[str, int]]:
    """Create a new run; never invoke a target or overwrite an existing run."""
    config = config.resolve()
    raw = load_config(config)
    pipeline = raw.get("pipeline")
    judge = pipeline.get("judge") if isinstance(pipeline, dict) else None
    if not isinstance(judge, dict) or not judge.get("enabled", True):
        raise ValueError("judge-traces requires an enabled pipeline.judge")

    # Upstream stages are deliberately absent, including in the archived config.
    raw["pipeline"] = {"judge": dict(judge)}
    ctx = load_runtime_context(raw, config, stage_modules=STAGES)
    resolved_judge = resolve_stage_paths(
        judge, cfg_path=config, artifacts_root=ctx["artifacts_root"]
    )
    taxonomy_path = Path(
        resolved_judge.get("taxonomy_path") or ctx["suite_root"] / "taxonomy.json"
    )
    taxonomy = json.loads(taxonomy_path.read_text(encoding="utf-8"))
    categories = (
        taxonomy.get("behavior_categories") if isinstance(taxonomy, dict) else None
    )
    if not isinstance(categories, list) or not categories:
        raise ValueError("taxonomy must contain non-empty behavior_categories")
    for category in categories:
        if (
            not isinstance(category, dict)
            or not isinstance(category.get("name"), str)
            or not category["name"].strip()
            or not isinstance(category.get("definition"), str)
            or not category["definition"].strip()
            or not isinstance(category.get("permissible"), bool)
        ):
            raise ValueError(
                "Each taxonomy category requires name, definition, and boolean permissible"
            )
    evaluation = ctx["evaluation"]
    build_judge_contract(
        template=JUDGE_SYSTEM_PROMPT,
        policy_raw=taxonomy,
        judge_dimensions=evaluation.judge.dimensions,
        disabled_dimensions=evaluation.judge.disabled_dimensions,
    )

    run_root = output.resolve() if output else ctx["run_root"]
    suite_root = run_root.parent
    raw.update(
        suite=suite_root.name,
        run=run_root.name,
        results_dir=str(suite_root.parent),
        artifacts_root=str(ctx["artifacts_root"]),
    )
    raw["pipeline"]["judge"].update(
        inference_set_path=str(run_root / "inference_set.jsonl"),
        taxonomy_path=str(suite_root / "taxonomy.json"),
        save_dir=str(run_root),
    )
    # Resolve presets now so replay uses the rubric that was actually approved.
    raw["pipeline"]["judge"].pop("preset", None)
    raw["pipeline"]["judge"]["model"] = asdict(evaluation.judge.model)
    raw["pipeline"]["judge"]["dimensions"] = {
        dimension["name"]: {
            key: value for key, value in dimension.items() if key != "name"
        }
        for dimension in evaluation.judge.dimensions
    }
    for dimension in raw["pipeline"]["judge"]["dimensions"].values():
        if scale := dimension.get("scale"):
            dimension["scale"] = {
                "type": "ordinal",
                "values": {grade["value"]: grade["label"] for grade in scale["values"]},
            }
    snapshot = run_root / "config.yaml"
    load_runtime_context(raw, snapshot, stage_modules=STAGES)
    if run_root.exists():
        raise ValueError(
            f"Run directory already exists: {run_root}. Select a new run or --output."
        )
    if (suite_root / "latest.json").exists():
        raise ValueError(
            f"{suite_root} contains generated artifact versions. Select a dedicated trace suite."
        )
    suite_taxonomy = suite_root / "taxonomy.json"
    if suite_taxonomy.exists():
        existing = json.loads(suite_taxonomy.read_text(encoding="utf-8"))
        if existing != taxonomy:
            raise ValueError(
                f"Taxonomy differs from {suite_taxonomy}. Select a new suite."
            )

    source_sha256 = hashlib.sha256(traces.read_bytes()).hexdigest()
    rows = parse_otel_traces(traces, group_by=group_by, include_inputs=True)
    if hashlib.sha256(traces.read_bytes()).hexdigest() != source_sha256:
        raise ValueError(
            "Trace file changed during import. Retry with a stable export."
        )
    if not rows:
        raise ValueError(
            "No conversations found in traces. Check your group_by attribute."
        )
    for row in rows:
        session_id = row["metadata"]["session_id"]
        if not session_id.strip() or any(
            not identifier.strip()
            for key in ("trace_ids", "span_ids")
            for identifier in row["metadata"][key]
        ):
            raise ValueError(
                "Imported conversations require non-empty session, trace, and span IDs"
            )
        identity = json.dumps([group_by, session_id], ensure_ascii=False)
        row.update(
            type="scenario",
            test_case_id="trace_"
            + hashlib.sha256(identity.encode("utf-8")).hexdigest(),
            behavior=ctx.get("behavior_name") or "",
        )
        row["metadata"]["source_sha256"] = source_sha256
        for event in row["events"]:
            edit = event["edit"]
            if edit["type"] == "tool_call":
                if not isinstance(edit.get("tool_args"), dict):
                    edit["tool_args"] = {"raw": edit.get("tool_args")}
                if not isinstance(edit.get("tool_result", ""), str):
                    edit["tool_result"] = json.dumps(
                        edit["tool_result"], ensure_ascii=False
                    )
                if edit.get("tool_call_id"):
                    event.setdefault("raw", {})["tool_call_id"] = edit["tool_call_id"]
            TranscriptEvent.model_validate(event)
        if not row["metadata"]["target_evidence_present"]:
            row["stop_reason"] = "trace_evidence_missing"

    run_root.mkdir(parents=True, exist_ok=False)
    try:
        with suite_taxonomy.open("x", encoding="utf-8") as handle:
            json.dump(taxonomy, handle, ensure_ascii=False, indent=2)
    except FileExistsError:
        if json.loads(suite_taxonomy.read_text(encoding="utf-8")) != taxonomy:
            raise ValueError(
                f"Taxonomy changed in {suite_taxonomy}. Select a new suite."
            ) from None
    write_jsonl(run_root / "inference_set.jsonl", rows)
    write_json(
        run_root / "trace_import.json",
        {
            "source_sha256": source_sha256,
            "group_by": group_by,
            "conversation_count": len(rows),
            "taxonomy_sha256": hashlib.sha256(
                json.dumps(taxonomy, sort_keys=True, ensure_ascii=False).encode("utf-8")
            ).hexdigest(),
        },
    )
    snapshot.write_text(
        yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )

    code = run_pipeline(config=str(snapshot))
    scores = load_jsonl(run_root / "scores.jsonl")
    counts: dict[str, int] = {
        "imported": len(rows),
        "unscored": len(rows) - len(scores),
    }
    for score in scores:
        status = infer_judge_status(score)
        counts[status] = counts.get(status, 0) + 1
    # A partial result must not become a successful import even if the normal
    # judge stage tolerates a small number of provider errors.
    if counts["unscored"] or any(
        count
        for status, count in counts.items()
        if status not in {"imported", "ok", "unscored"}
    ):
        code = 1
    manifest_path = run_root / "manifest.json"
    if code and manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["status"] = "failed"
        manifest["stages"]["judge"] = "failed"
        write_json(manifest_path, manifest)
    return code, run_root, counts
