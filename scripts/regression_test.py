"""PR regression test — real implementation.

Runs the pipeline at the baseline + treatment commits against the two
golden risk specs (``tests/regression/config_{safety,quality}.yaml``) with
a shared seed budget, computes the science-efficacy metrics, runs paired
statistical tests (McNemar for per-seed binary, bootstrap delta for
suite-level), and emits a Holm-Bonferroni-gated decision report consumed
by the ``science.yml`` workflow's PR summary step.

Determinism contract
--------------------
Stages ``policy``, ``design``, ``seeds`` are FROZEN across baseline +
treatment unless the diff shows a file that affects those stages. This
keeps the comparison a true paired-by-seed-id comparison of rollout +
judge changes. Set ``--rerun-upstream-stages`` to force regeneration on
both commits (e.g. for prompt-tuning PRs).

Caching
-------
Baseline runs are cached by ``(base_sha, config_hash, judge_model,
n_seeds, script_hash)``. PRs against the same base commit reuse the
cached baseline transcripts/scores; only the treatment is re-run.

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
from pathlib import Path
from typing import Any, Iterable

import yaml

# Allow ``python scripts/regression_test.py`` invocation in addition to
# ``python -m scripts.regression_test``. When run directly, sys.path[0]
# is the script's own dir, so ``scripts.x`` imports fail without this.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from p2m.core.io import SCORES_FILE, load_jsonl

from scripts.regression_decision import (
    DECISION_BLOCK,
    DEFAULT_ALPHA,
    decide,
)
from scripts.regression_metrics import compute_all

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIGS: tuple[Path, ...] = (
    REPO_ROOT / "tests" / "regression" / "config_safety.yaml",
    REPO_ROOT / "tests" / "regression" / "config_quality.yaml",
)

# Files whose change requires rerunning upstream (cacheable) stages.
UPSTREAM_STAGE_GLOBS: tuple[str, ...] = (
    "p2m/stages/policy.py",
    "p2m/stages/design.py",
    "p2m/stages/seeds.py",
    "p2m/core/artifact_cache.py",
    "prompts/policy_system.md",
    "prompts/seeds_system.md",
    "prompts/seeds_user.md",
    "prompts/design_system.md",
)


# ── CLI ────────────────────────────────────────────────────────────────────


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--baseline", required=True, help="Baseline commit SHA")
    p.add_argument("--treatment", required=True, help="Treatment commit SHA")
    p.add_argument(
        "--seeds",
        type=int,
        default=100,
        help="Per-spec seed budget (split equally across prompt + scenario)",
    )
    p.add_argument(
        "--configs",
        nargs="+",
        type=Path,
        default=list(DEFAULT_CONFIGS),
        help="Risk-spec config YAMLs",
    )
    p.add_argument(
        "--artifacts-dir",
        type=Path,
        default=Path("artifacts/regression"),
    )
    p.add_argument(
        "--baseline-cache-dir",
        type=Path,
        default=None,
        help="If set, reuse cached baseline scores instead of rerunning",
    )
    p.add_argument(
        "--rerun-upstream-stages",
        action="store_true",
        help="Force rerunning policy/design/seeds on both commits",
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
        help="Per-test significance level for the gate",
    )
    p.add_argument(
        "--judge-model",
        default="azure/gpt-5.4",
        help="Judge model override (long-context required for realistic agents)",
    )
    p.add_argument(
        "--alpha-canonical-only",
        action="store_true",
        default=True,
        help="Apply Holm-Bonferroni only over the 6 canonical metrics (default)",
    )
    return p.parse_args(argv)


# ── Change detection ───────────────────────────────────────────────────────


def changed_files(baseline: str, treatment: str) -> list[str]:
    try:
        out = subprocess.check_output(
            ["git", "diff", "--name-only", f"{baseline}..{treatment}"],
            cwd=REPO_ROOT,
            text=True,
        )
        return [line.strip() for line in out.splitlines() if line.strip()]
    except subprocess.CalledProcessError as exc:
        log.warning("git diff failed (%s); assuming all stages affected", exc)
        return ["__assume_all__"]


def upstream_stages_dirty(files: list[str]) -> bool:
    if "__assume_all__" in files:
        return True
    return any(f in UPSTREAM_STAGE_GLOBS for f in files)


# ── Pipeline runner ────────────────────────────────────────────────────────


def _config_hash(path: Path, *, n_seeds: int, judge_model: str) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    h.update(f"\nseeds={n_seeds}\njudge={judge_model}\n".encode())
    return h.hexdigest()[:16]


def _suite_dir_for(config: Path, commit_sha: str, n_seeds: int, judge_model: str) -> Path:
    cfg_hash = _config_hash(config, n_seeds=n_seeds, judge_model=judge_model)
    label = config.stem  # "config_safety" / "config_quality"
    return REPO_ROOT / "artifacts" / "regression-runs" / f"{label}-{commit_sha[:7]}-{cfg_hash}"


def _worktree_path_for(commit_sha: str) -> Path:
    return REPO_ROOT / ".regression-worktrees" / commit_sha[:12]


def ensure_worktree(commit_sha: str) -> Path:
    """Create (or reuse) a git worktree pinned at ``commit_sha``.

    Worktrees let baseline + treatment runs use the actual file tree of
    each commit (including ``p2m/`` source, ``prompts/``, configs) without
    mutating the main checkout. Without this, both runs would share the
    treatment's source code and the comparison would be a trivial no-op.
    """
    wt = _worktree_path_for(commit_sha)
    if wt.exists():
        log.info("reusing worktree at %s", wt)
        return wt
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
    n_seeds: int,
    judge_model: str,
    target_dir: Path,
) -> Path:
    """Materialise a per-run YAML with the requested overrides.

    The CLI only accepts ``--config``; sample sizes, judge model, and
    output location (suite/run) all come from the YAML body. We mutate
    a copy and write it inside ``target_dir`` (typically the worktree)
    so the run is fully self-contained.
    """
    cfg = yaml.safe_load(source.read_text(encoding="utf-8"))
    cfg["suite"] = suite_name
    cfg["run"] = run_label
    seeds_cfg = cfg.setdefault("pipeline", {}).setdefault("seeds", {})
    half = n_seeds // 2
    seeds_cfg.setdefault("prompt", {})["sample_size"] = half
    seeds_cfg.setdefault("scenario", {})["sample_size"] = n_seeds - half
    judge = cfg["pipeline"].setdefault("judge", {}).setdefault("model", {})
    judge["name"] = judge_model
    target_dir.mkdir(parents=True, exist_ok=True)
    out = target_dir / f"_regression_{source.stem}_{run_label}.yaml"
    out.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    return out


def run_pipeline(
    config: Path,
    *,
    commit_sha: str,
    n_seeds: int,
    judge_model: str,
    extra_overrides: dict[str, Any] | None = None,
) -> Path:
    """Run ``p2m run`` against one config from a worktree at ``commit_sha``.

    Pipeline outputs land in ``<worktree>/artifacts/results/<suite>/<run>/``;
    we copy the run dir to ``REPO_ROOT/artifacts/regression-runs/`` so they
    survive worktree teardown and the workflow's actions/cache step can
    persist them across PR runs.
    """
    suite_dir = _suite_dir_for(config, commit_sha, n_seeds, judge_model)
    run_label = f"reg-{commit_sha[:7]}"
    final_run_dir = suite_dir / run_label
    if (final_run_dir / SCORES_FILE).exists():
        log.info("scores already exist for %s — skipping rerun", commit_sha[:7])
        return final_run_dir

    suite_dir.mkdir(parents=True, exist_ok=True)
    if extra_overrides:
        log.warning("extra_overrides not yet wired through temp YAML: %s", extra_overrides)

    worktree = ensure_worktree(commit_sha)
    rel = config.resolve().relative_to(REPO_ROOT)
    config_in_wt = worktree / rel
    suite_name = suite_dir.name  # unique per (config, commit, hash) tuple
    rendered = _render_config(
        config_in_wt,
        suite_name=suite_name,
        run_label=run_label,
        n_seeds=n_seeds,
        judge_model=judge_model,
        # Sibling files (concept markdown, etc.) are resolved relative
        # to the YAML's parent dir, so emit the temp config alongside
        # the source.
        target_dir=config_in_wt.parent,
    )

    cmd = [
        sys.executable, "-m", "p2m.cli", "run",
        "--config", str(rendered),
    ]
    # Prepend the worktree to PYTHONPATH so ``import p2m`` resolves to
    # the worktree's source (and ``BASE_DIR`` -> worktree's prompts/),
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
    src_run_dir = worktree / "artifacts" / "results" / suite_name / run_label
    if not src_run_dir.exists():
        raise RuntimeError(
            f"pipeline did not write expected run dir at {src_run_dir}"
        )
    if final_run_dir.exists():
        shutil.rmtree(final_run_dir)
    final_run_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src_run_dir, final_run_dir)
    log.info("copied %s -> %s", src_run_dir, final_run_dir)
    return final_run_dir


def _scores_for(run_dir: Path) -> list[dict[str, Any]]:
    scores_path = run_dir / SCORES_FILE
    if not scores_path.exists():
        log.warning("no scores at %s", scores_path)
        return []
    return list(load_jsonl(scores_path))


def _policy_for(run_dir: Path) -> dict[str, Any] | None:
    for candidate in ("policy.json", "taxonomy.json"):
        path = run_dir / candidate
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    return None


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
        f"alpha (per-test) = {report['alpha']}, n_seeds = {report.get('n_seeds')}"
    )
    lines.append("")
    lines.append("| Metric | Granularity | Direction | Baseline | Treatment | Δ | p | Effect |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for r in report["results"]:
        lines.append(
            f"| {r['metric_name']} | {r['granularity']} | {r['direction'] or '—'} | "
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

    files = changed_files(args.baseline, args.treatment)
    rerun_upstream = args.rerun_upstream_stages or upstream_stages_dirty(files)
    log.info(
        "changed files=%d, upstream stages dirty=%s, rerun_upstream=%s",
        len(files), upstream_stages_dirty(files), rerun_upstream,
    )

    per_config_results: dict[str, dict[str, Any]] = {}
    aggregated_baseline: list[dict[str, Any]] = []
    aggregated_treatment: list[dict[str, Any]] = []
    policy_for_metrics: dict[str, Any] | None = None
    worktrees_created: set[str] = set()

    try:
        for config in args.configs:
            log.info("=== config: %s ===", config.name)
            worktrees_created.add(args.baseline)
            baseline_dir = run_pipeline(
                config,
                commit_sha=args.baseline,
                n_seeds=args.seeds,
                judge_model=args.judge_model,
            )
            worktrees_created.add(args.treatment)
            treatment_dir = run_pipeline(
                config,
                commit_sha=args.treatment,
                n_seeds=args.seeds,
                judge_model=args.judge_model,
            )
            baseline_rows = _scores_for(baseline_dir)
            treatment_rows = _scores_for(treatment_dir)
            policy = _policy_for(baseline_dir) or _policy_for(treatment_dir)
            if policy_for_metrics is None:
                policy_for_metrics = policy
            per_config_results[config.name] = {
                "baseline_dir": str(baseline_dir),
                "treatment_dir": str(treatment_dir),
                "baseline_n": len(baseline_rows),
                "treatment_n": len(treatment_rows),
            }
            aggregated_baseline.extend(baseline_rows)
            aggregated_treatment.extend(treatment_rows)
    finally:
        for sha in worktrees_created:
            remove_worktree(sha)

    baseline_metrics = compute_all(aggregated_baseline, policy_for_metrics)
    treatment_metrics = compute_all(aggregated_treatment, policy_for_metrics)
    report = decide(
        baseline_metrics,
        treatment_metrics,
        alpha=args.alpha,
        n_seeds=args.seeds * len(args.configs),
    )
    report["per_config"] = per_config_results
    report["baseline_sha"] = args.baseline
    report["treatment_sha"] = args.treatment
    report["upstream_stages_rerun"] = rerun_upstream
    report["judge_model"] = args.judge_model

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
