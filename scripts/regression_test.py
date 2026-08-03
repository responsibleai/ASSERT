"""PR regression test — real implementation.

Runs the pipeline at the baseline + treatment commits against the two
golden failure-mode configs (``tests/regression/config_{safety,quality}.yaml``) with
a shared test-set size, computes policy-violation rates for relevant permissible
and non-permissible behaviors, runs paired McNemar tests, and emits a per-metric
decision report consumed by the ``science.yml`` workflow's PR summary step.

Determinism contract
--------------------
The baseline generates taxonomy and test-set artifacts once. Those exact files
are copied into the treatment worktree, where upstream stages are disabled.
This keeps inference and judge comparisons paired by identical test-case IDs
and content.

Caching
-------
Baseline runs are cached by ``(base_sha, config_hash, models, test_set_size)``.
Treatment outputs are always rerun and are never included in the workflow cache.

Output
------
Writes ``regression_report.json`` (machine) and ``regression_report.md``
(reviewer) into ``--artifacts-dir``. Always exits 0 in advisory mode
(workflow has ``continue-on-error: true``); set ``--enforce`` to make a
``BLOCK`` decision exit nonzero.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

# Allow ``python scripts/regression_test.py`` invocation in addition to
# ``python -m scripts.regression_test``. When run directly, sys.path[0]
# is the script's own dir, so ``scripts.x`` imports fail without this.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from assert_ai.core.io import SCORES_FILE, load_jsonl

from scripts.regression_decision import (
    DECISION_BLOCK,
    DEFAULT_ALPHA,
    decide,
)
from scripts.regression_metrics import compute_all

log = logging.getLogger(__name__)

DEFAULT_CONFIGS: tuple[Path, ...] = (
    REPO_ROOT / "tests" / "regression" / "config_safety.yaml",
    REPO_ROOT / "tests" / "regression" / "config_quality.yaml",
)

SHARED_UPSTREAM_FILES: tuple[str, ...] = (
    "taxonomy.json",
    "systematization.json",
    "test_set.jsonl",
    "stratification.json",
)
REQUIRED_SHARED_UPSTREAM_FILES: tuple[str, ...] = ("taxonomy.json", "test_set.jsonl")
DEFAULT_BASELINE_CACHE_DIR = Path("artifacts/regression-baselines")
TREATMENT_RESULTS_DIR = Path("artifacts/regression-runs")
TRAVEL_PLANNER_SYNC_TARGET = "examples.travel_planner_langgraph.auto_trace:chat_sync"
REGRESSION_ASYNC_TARGET = "_science_regression_target:chat"
REGRESSION_TARGET_MODULE = """\
from examples.travel_planner_langgraph import auto_trace as _auto_trace
from examples.travel_planner_langgraph.agent import chat
"""


@dataclass(frozen=True)
class PipelineArtifacts:
    suite_dir: Path
    run_dir: Path


# ── CLI ────────────────────────────────────────────────────────────────────


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--baseline", required=True, help="Baseline commit SHA")
    p.add_argument("--treatment", required=True, help="Treatment commit SHA")
    p.add_argument(
        "--test_set",
        type=int,
        default=100,
        help="Per-spec test-set size (split equally across prompt + scenario)",
    )
    p.add_argument(
        "--configs",
        nargs="+",
        type=Path,
        default=list(DEFAULT_CONFIGS),
        help="Failure-mode config YAMLs",
    )
    p.add_argument(
        "--artifacts-dir",
        type=Path,
        default=Path("artifacts/regression"),
    )
    p.add_argument(
        "--baseline-cache-dir",
        type=Path,
        default=DEFAULT_BASELINE_CACHE_DIR,
        help="Directory used for reusable baseline runs and shared test sets",
    )
    p.add_argument(
        "--enforce",
        action="store_true",
        help="Exit nonzero on BLOCK decision (default: advisory exit 0)",
    )
    p.add_argument(
        "--alpha",
        type=float,
        default=DEFAULT_ALPHA,
        help="Per-metric significance level for the gate",
    )
    p.add_argument(
        "--judge-model",
        default="azure/gpt-5.4",
        help="Judge model override (long-context required for realistic agents)",
    )
    p.add_argument(
        "--upstream-model",
        default="azure/gpt-5.4",
        help=(
            "Override the model used for systematize + test_set + tester stages. "
            "Defaults to gpt-5.4: configs ship with gpt-5.4-mini for cost, "
            "but adversarial scenario test-case schemas trip its content filter "
            "/ structured-output handling, dropping payloads silently."
        ),
    )
    return p.parse_args(argv)


# ── Pipeline runner ────────────────────────────────────────────────────────


def _config_hash(
    path: Path,
    *,
    test_set_size: int,
    judge_model: str,
    upstream_model: str,
) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    h.update(
        (
            f"\ntest_set={test_set_size}\njudge={judge_model}"
            f"\nupstream={upstream_model}\n"
        ).encode()
    )
    return h.hexdigest()[:16]


def _resolve_storage_root(path: Path) -> Path:
    expanded = path.expanduser()
    return expanded if expanded.is_absolute() else REPO_ROOT / expanded


def _suite_dir_for(
    storage_root: Path,
    config: Path,
    commit_sha: str,
    test_set_size: int,
    judge_model: str,
    upstream_model: str,
) -> Path:
    cfg_hash = _config_hash(
        config,
        test_set_size=test_set_size,
        judge_model=judge_model,
        upstream_model=upstream_model,
    )
    label = config.stem  # "config_safety" / "config_quality"
    return _resolve_storage_root(storage_root) / f"{label}-{commit_sha[:7]}-{cfg_hash}"


def _worktree_path_for(commit_sha: str) -> Path:
    return REPO_ROOT / ".regression-worktrees" / commit_sha[:12]


def ensure_worktree(commit_sha: str) -> Path:
    """Create a clean detached worktree pinned at ``commit_sha``.

    Worktrees let baseline + treatment runs use the actual file tree of
    each commit (including ``assert_ai/`` source, packaged prompts, configs) without
    mutating the main checkout. Without this, both runs would share the
    treatment's source code and the comparison would be a trivial no-op.
    """
    wt = _worktree_path_for(commit_sha)
    if wt.exists():
        log.info("removing stale worktree at %s", wt)
        remove_worktree(commit_sha)
    wt.parent.mkdir(parents=True, exist_ok=True)
    log.info("creating worktree for %s at %s", commit_sha[:7], wt)
    subprocess.check_call(
        ["git", "worktree", "add", "--detach", str(wt), commit_sha],
        cwd=REPO_ROOT,
    )
    return wt


def remove_worktree(commit_sha: str) -> None:
    wt = _worktree_path_for(commit_sha)
    if not wt.exists():
        return
    try:
        subprocess.check_call(
            ["git", "worktree", "remove", "--force", str(wt)],
            cwd=REPO_ROOT,
        )
    except subprocess.CalledProcessError:
        log.warning("worktree remove failed for %s; falling back to rmtree", wt)
        shutil.rmtree(wt, ignore_errors=True)
        subprocess.call(["git", "worktree", "prune"], cwd=REPO_ROOT)


def _render_config(
    source: Path,
    *,
    suite_name: str,
    run_label: str,
    test_set_size: int,
    judge_model: str,
    upstream_model: str,
    target_dir: Path,
    freeze_upstream: bool = False,
    target_callable_override: str | None = None,
) -> Path:
    """Materialise a per-run YAML with the requested overrides.

    The CLI only accepts ``--config``; sample sizes, models, and output
    location (dataset/run) all come from the YAML body. We mutate a copy
    and write it inside ``target_dir`` (typically the worktree's
    ``tests/regression/``) so sibling concept markdown is found and the
    run is fully self-contained.

    ``upstream_model`` overrides the model used for behavior categorization,
    test-set generation, and tester stages (per-stage). The default ``gpt-5.4-mini`` in
    the source configs has been observed to crash on adversarial
    scenario test-case schemas (returns null/empty parsed payloads — likely
    content-filter rejection). Bumping all upstream stages to the
    long-context judge avoids the failure at modest cost.
    """
    cfg = yaml.safe_load(source.read_text(encoding="utf-8"))
    cfg["suite"] = suite_name
    cfg["run"] = run_label

    pipeline = cfg.setdefault("pipeline", {})

    # Sample sizes
    test_set_cfg = pipeline.setdefault("test_set", {})
    half = test_set_size // 2
    test_set_cfg.setdefault("prompt", {})["sample_size"] = half
    test_set_cfg.setdefault("scenario", {})["sample_size"] = test_set_size - half

    # Models — judge first
    pipeline.setdefault("judge", {}).setdefault("model", {})["name"] = judge_model

    # Upstream stages: behavior categorization, both test-case generators, tester.
    # Test-case generation must have enough max_tokens for the full batch:
    # at test_set=200 + behavior_count=5, each call produces ~20–40
    # test cases with rich descriptions. The project default
    # (DEFAULT_GENERATION_MAX_TOKENS=3000) truncates these, leaving an
    # incomplete JSON that fails to parse → "invalid test_set payload".
    pipeline.setdefault("systematize", {}).setdefault("model", {})["name"] = upstream_model
    prompt_model = test_set_cfg.setdefault("prompt", {}).setdefault("model", {})
    prompt_model["name"] = upstream_model
    prompt_model["max_tokens"] = 16000
    scenario_model = test_set_cfg.setdefault("scenario", {}).setdefault("model", {})
    scenario_model["name"] = upstream_model
    scenario_model["max_tokens"] = 16000
    if freeze_upstream:
        pipeline.setdefault("systematize", {})["enabled"] = False
        test_set_cfg["enabled"] = False
    inference = pipeline.setdefault("inference", {})
    target = inference.setdefault("target", {})
    if (
        target_callable_override
        and target.get("callable") == TRAVEL_PLANNER_SYNC_TARGET
    ):
        target["callable"] = target_callable_override
    inference.setdefault("tester", {}).setdefault("model", {})["name"] = upstream_model
    inference["tool_timeout_s"] = min(
        float(inference.get("tool_timeout_s") or 180),
        180.0,
    )
    # Bump inference concurrency so test_set=200 finishes in workflow timeout.
    inference["concurrency"] = max(int(inference.get("concurrency", 2) or 2), 10)

    target_dir.mkdir(parents=True, exist_ok=True)
    out = target_dir / f"_regression_{source.stem}_{run_label}.yaml"
    out.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    return out


def run_pipeline(
    config: Path,
    *,
    commit_sha: str,
    test_set_size: int,
    judge_model: str,
    upstream_model: str,
    storage_root: Path,
    reuse_existing: bool,
    frozen_upstream_dir: Path | None = None,
) -> PipelineArtifacts:
    """Run ``assert-ai run`` against one config from a worktree at ``commit_sha``.

    Pipeline outputs land in ``<worktree>/artifacts/results/<suite>/<run>/``;
    selected suite artifacts are copied outside the worktree so they survive
    teardown. When ``frozen_upstream_dir`` is provided, its taxonomy and test
    set are copied into the treatment suite and upstream stages are disabled.
    """
    suite_dir = _suite_dir_for(
        storage_root,
        config,
        commit_sha,
        test_set_size,
        judge_model,
        upstream_model,
    )
    run_label = f"reg-{commit_sha[:7]}"
    final_run_dir = suite_dir / run_label
    has_shared_upstream = all(
        (suite_dir / name).exists() for name in REQUIRED_SHARED_UPSTREAM_FILES
    )
    if reuse_existing and (final_run_dir / SCORES_FILE).exists() and has_shared_upstream:
        log.info("scores already exist for %s — skipping rerun", commit_sha[:7])
        return PipelineArtifacts(suite_dir=suite_dir, run_dir=final_run_dir)

    if suite_dir.exists():
        shutil.rmtree(suite_dir)
    suite_dir.mkdir(parents=True, exist_ok=True)

    worktree = ensure_worktree(commit_sha)
    rel = config.resolve().relative_to(REPO_ROOT)
    config_in_wt = worktree / rel
    (worktree / "_science_regression_target.py").write_text(
        REGRESSION_TARGET_MODULE,
        encoding="utf-8",
    )
    suite_name = suite_dir.name  # unique per (config, commit, hash) tuple
    rendered = _render_config(
        config_in_wt,
        suite_name=suite_name,
        run_label=run_label,
        test_set_size=test_set_size,
        judge_model=judge_model,
        upstream_model=upstream_model,
        # Sibling files (concept markdown, etc.) are resolved relative
        # to the YAML's parent dir, so emit the temp config alongside
        # the source.
        target_dir=config_in_wt.parent,
        freeze_upstream=frozen_upstream_dir is not None,
        target_callable_override=REGRESSION_ASYNC_TARGET,
    )

    worktree_suite_dir = worktree / "artifacts" / "results" / suite_name
    if frozen_upstream_dir is not None:
        _copy_shared_upstream_artifacts(
            frozen_upstream_dir,
            worktree_suite_dir,
        )

    cmd = [
        sys.executable, "-m", "assert_ai.cli", "run",
        "--config", str(rendered),
    ]
    # Prepend the worktree to PYTHONPATH so ``import assert_ai`` resolves to
    # the worktree's source and packaged prompt resources,
    # NOT the editable-install pointing at the main checkout. Without
    # this, baseline + treatment runs would import the same source code.
    env = os.environ.copy()
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        str(worktree) + (os.pathsep + existing_pp if existing_pp else "")
    )
    log.info("running: %s (cwd=%s, PYTHONPATH=%s)", " ".join(cmd), worktree, worktree)
    subprocess.check_call(cmd, cwd=worktree, env=env)

    # Copy the worktree's result dir into REPO_ROOT so it survives teardown
    # and the workflow cache layer can persist it.
    src_suite_dir = worktree / "artifacts" / "results" / suite_name
    src_run_dir = src_suite_dir / run_label
    if not src_run_dir.exists():
        raise RuntimeError(
            f"pipeline did not write expected run dir at {src_run_dir}"
        )
    _copy_shared_upstream_artifacts(src_suite_dir, suite_dir)
    shutil.copytree(src_run_dir, final_run_dir)
    log.info("copied %s -> %s", src_run_dir, final_run_dir)
    return PipelineArtifacts(suite_dir=suite_dir, run_dir=final_run_dir)


def _copy_shared_upstream_artifacts(source: Path, destination: Path) -> None:
    missing = [
        name for name in REQUIRED_SHARED_UPSTREAM_FILES if not (source / name).exists()
    ]
    if missing:
        raise RuntimeError(
            f"shared upstream artifacts missing from {source}: {', '.join(missing)}"
        )
    destination.mkdir(parents=True, exist_ok=True)
    for name in SHARED_UPSTREAM_FILES:
        src = source / name
        if src.exists():
            shutil.copy2(src, destination / name)


def _assert_shared_upstream_identical(baseline_suite: Path, treatment_suite: Path) -> None:
    for name in REQUIRED_SHARED_UPSTREAM_FILES:
        baseline_path = baseline_suite / name
        treatment_path = treatment_suite / name
        baseline_hash = hashlib.sha256(baseline_path.read_bytes()).hexdigest()
        treatment_hash = hashlib.sha256(treatment_path.read_bytes()).hexdigest()
        if baseline_hash != treatment_hash:
            raise RuntimeError(
                f"paired comparison requires identical {name}; "
                f"baseline={baseline_hash[:12]} treatment={treatment_hash[:12]}"
            )


def _scores_for(run_dir: Path) -> list[dict[str, Any]]:
    scores_path = run_dir / SCORES_FILE
    if not scores_path.exists():
        log.warning("no scores at %s", scores_path)
        return []
    return list(load_jsonl(scores_path))


def _paired_test_case_count(
    baseline_rows: list[dict[str, Any]],
    treatment_rows: list[dict[str, Any]],
) -> int:
    def ids(rows: list[dict[str, Any]]) -> set[str]:
        return {
            str(row.get("test_case_id") or row.get("id"))
            for row in rows
            if row.get("test_case_id") is not None or row.get("id") is not None
        }

    return len(ids(baseline_rows) & ids(treatment_rows))


def _policy_for(suite_dir: Path) -> dict[str, Any] | None:
    path = suite_dir / "taxonomy.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _combine_config_reports(
    per_config: dict[str, dict[str, Any]],
    *,
    alpha: float,
    test_set_size: int,
) -> dict[str, Any]:
    decision_rank = {"PASS": 0, "WARN": 1, "BLOCK": 2}
    overall_decision = "PASS"
    reasons: list[str] = []
    results: list[dict[str, Any]] = []
    baseline_metrics: dict[str, Any] = {}
    treatment_metrics: dict[str, Any] = {}

    for config_name, payload in per_config.items():
        config_report = payload["report"]
        config_decision = config_report["decision"]["decision"]
        if decision_rank[config_decision] > decision_rank[overall_decision]:
            overall_decision = config_decision
        if config_decision != "PASS":
            reasons.extend(
                f"{config_name}: {reason}"
                for reason in config_report["decision"]["reasons"]
            )
        for result in config_report["results"]:
            results.append({"config": config_name, **result})
        baseline_metrics[config_name] = config_report["baseline_metrics"]
        treatment_metrics[config_name] = config_report["treatment_metrics"]

    if not reasons:
        reasons.append("No config showed a canonical metric regression beyond its threshold.")

    return {
        "schema_version": 2,
        "alpha": alpha,
        "test_set_size": test_set_size,
        "paired_test_set_source": "baseline",
        "results": results,
        "baseline_metrics": baseline_metrics,
        "treatment_metrics": treatment_metrics,
        "per_config": per_config,
        "decision": {
            "decision": overall_decision,
            "reasons": reasons,
        },
    }


# ── Reporting ──────────────────────────────────────────────────────────────


_ICONS = {
    "Improved": "✅",
    "Degraded": "❌",
    "Inconclusive": "⚠️",
    "TooFewSamples": "📊",
    "Info": "ℹ️",
    "PASS": "✅",
    "WARN": "⚠️",
    "BLOCK": "❌",
}


def render_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = []
    decision = report["decision"]["decision"]
    lines.append(f"## 🧪 Regression Test — {_ICONS.get(decision, '?')} {decision}")
    lines.append("")
    lines.append(
        f"per-metric alpha = {report['alpha']}, "
        f"test_set_size = {report.get('test_set_size')}"
    )
    lines.append("")
    lines.append(
        "| Config | Metric | Granularity | Direction | Baseline | Treatment | "
        "Δ | Regression p | Effect |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for r in report["results"]:
        lines.append(
            f"| {r['config']} | {r['metric_name']} | {r['granularity']} | "
            f"{r['direction'] or '—'} | "
            f"{_fmt(r['baseline_value'])} | {_fmt(r['treatment_value'])} | "
            f"{_fmt(r['mean_diff'])} | {_fmt(r['p_value'])} | "
            f"{_ICONS.get(r['effect'], '?')} {r['effect']} |"
        )
    lines.append("")
    lines.append("**Reasons:**")
    for reason in report["decision"]["reasons"]:
        lines.append(f"- {reason}")
    return "\n".join(lines)


def _fmt(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.4f}"
    return str(v)


# ── Main ───────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = _parse_args(argv)
    args.artifacts_dir.mkdir(parents=True, exist_ok=True)

    per_config_results: dict[str, dict[str, Any]] = {}
    baseline_by_config: dict[Path, PipelineArtifacts] = {}
    worktrees_created: set[str] = set()

    try:
        for config in args.configs:
            log.info("=== baseline config: %s ===", config.name)
            worktrees_created.add(args.baseline)
            baseline_by_config[config] = run_pipeline(
                config,
                commit_sha=args.baseline,
                test_set_size=args.test_set,
                judge_model=args.judge_model,
                upstream_model=args.upstream_model,
                storage_root=args.baseline_cache_dir,
                reuse_existing=True,
            )

        baseline_cache_root = _resolve_storage_root(args.baseline_cache_dir)
        baseline_cache_root.mkdir(parents=True, exist_ok=True)
        (baseline_cache_root / ".complete").write_text(
            f"{args.baseline}\n",
            encoding="utf-8",
        )

        for config in args.configs:
            log.info("=== treatment config: %s ===", config.name)
            baseline_artifacts = baseline_by_config[config]
            worktrees_created.add(args.treatment)
            treatment_artifacts = run_pipeline(
                config,
                commit_sha=args.treatment,
                test_set_size=args.test_set,
                judge_model=args.judge_model,
                upstream_model=args.upstream_model,
                storage_root=TREATMENT_RESULTS_DIR,
                reuse_existing=False,
                frozen_upstream_dir=baseline_artifacts.suite_dir,
            )
            baseline_rows = _scores_for(baseline_artifacts.run_dir)
            treatment_rows = _scores_for(treatment_artifacts.run_dir)
            _assert_shared_upstream_identical(
                baseline_artifacts.suite_dir,
                treatment_artifacts.suite_dir,
            )
            paired_count = _paired_test_case_count(baseline_rows, treatment_rows)
            policy = _policy_for(baseline_artifacts.suite_dir)
            if policy is None:
                raise RuntimeError(
                    f"baseline taxonomy missing from {baseline_artifacts.suite_dir}"
                )
            config_report = decide(
                compute_all(baseline_rows, policy),
                compute_all(treatment_rows, policy),
                alpha=args.alpha,
                test_set_size=paired_count,
            )
            per_config_results[config.name] = {
                "baseline_dir": str(baseline_artifacts.run_dir),
                "treatment_dir": str(treatment_artifacts.run_dir),
                "taxonomy_path": str(baseline_artifacts.suite_dir / "taxonomy.json"),
                "test_set_path": str(baseline_artifacts.suite_dir / "test_set.jsonl"),
                "baseline_n": len(baseline_rows),
                "treatment_n": len(treatment_rows),
                "paired_n": paired_count,
                "report": config_report,
            }
    finally:
        for sha in worktrees_created:
            remove_worktree(sha)

    report = _combine_config_reports(
        per_config_results,
        alpha=args.alpha,
        test_set_size=args.test_set,
    )
    report["baseline_sha"] = args.baseline
    report["treatment_sha"] = args.treatment
    report["judge_model"] = args.judge_model
    report["upstream_model"] = args.upstream_model

    report_json = args.artifacts_dir / "regression_report.json"
    report_md = args.artifacts_dir / "regression_report.md"
    report_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    report_md.write_text(render_markdown(report), encoding="utf-8")
    print(f"[regression] wrote {report_json} and {report_md}")
    print(f"[regression] decision = {report['decision']['decision']}")

    if args.enforce and report["decision"]["decision"] == DECISION_BLOCK:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
