"""PR regression test — real implementation.

Runs the pipeline at the baseline + treatment commits against the two
golden failure-mode configs (``tests/regression/config_{safety,quality}.yaml``) with
a shared test-set size, computes the science-efficacy metrics, runs paired
statistical tests (McNemar for per-test-case binary, bootstrap delta for
dataset-level), and emits a Holm-Bonferroni-gated decision report consumed
by the ``science.yml`` workflow's PR summary step.

Determinism contract
--------------------
Stages ``systematize``, ``stratification``, ``test_set`` are FROZEN across baseline +
treatment unless the diff shows a file that affects those stages. This
keeps the comparison a true paired-by-test-case-id comparison of inference +
judge changes. Set ``--rerun-upstream-stages`` to force regeneration on
both commits (e.g. for prompt-tuning PRs).

Caching
-------
Baseline runs are cached by ``(base_sha, config_hash, judge_model,
test_set_size, script_hash)``. PRs against the same base commit reuse the
cached baseline inference outputs/scores; only the treatment is re-run.

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
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from p2m.core.io import SCORES_FILE, load_jsonl

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

# Files whose change requires rerunning upstream (cacheable) stages.
UPSTREAM_STAGE_FILES: tuple[str, ...] = (
    "p2m/stages/systematize.py",
    "p2m/stages/stratification.py",
    "p2m/stages/test_set.py",
    "p2m/core/artifact_cache.py",
    "prompts/systematize_system.md",
    "prompts/test_set_direct_single.md",
    "prompts/test_set_scenario_single.md",
    "prompts/test_set_stratification.md",
)


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
        default=None,
        help="If set, reuse cached baseline scores instead of rerunning",
    )
    p.add_argument(
        "--rerun-upstream-stages",
        action="store_true",
        help="Force rerunning systematize/stratification/test_set on both commits",
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
    return any(f in UPSTREAM_STAGE_FILES for f in files)


# ── Pipeline runner ────────────────────────────────────────────────────────


def _config_hash(path: Path, *, test_set_size: int, judge_model: str) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    h.update(f"\ntest_set={test_set_size}\njudge={judge_model}\n".encode())
    return h.hexdigest()[:16]


def _suite_dir_for(config: Path, commit_sha: str, test_set_size: int, judge_model: str) -> Path:
    cfg_hash = _config_hash(config, test_set_size=test_set_size, judge_model=judge_model)
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
        _apply_test_set_diagnostic(wt)
        return wt
    wt.parent.mkdir(parents=True, exist_ok=True)
    log.info("creating worktree for %s at %s", commit_sha[:7], wt)
    subprocess.check_call(
        ["git", "worktree", "add", "--detach", str(wt), commit_sha],
        cwd=REPO_ROOT,
    )
    _apply_test_set_diagnostic(wt)
    return wt


def _apply_test_set_diagnostic(worktree: Path) -> None:
    """Patch ``p2m/stages/test_set.py`` to print response details on invalid payloads."""
    target = worktree / "p2m" / "stages" / "test_set.py"
    if not target.exists():
        return
    try:
        text = target.read_text(encoding="utf-8")
    except OSError:
        return
    if "[DEBUG TEST_SET-FAIL]" in text:
        return
    sentinel = (
        '            if not isinstance(payload, dict) or not isinstance(payload.get("test_set"), list):\n'
        '                raise ValueError(f"{kind} test-case generation returned invalid test_set payload")\n'
    )
    if sentinel not in text:
        log.warning("test_set diagnostic sentinel not found in %s; skipping patch", target)
        return
    replacement = (
        '            if not isinstance(payload, dict) or not isinstance(payload.get("test_set"), list):\n'
        '                print(f"\\n[DEBUG TEST_SET-FAIL] kind={kind} behavior={behavior_name}", flush=True)\n'
        '                print(f"[DEBUG TEST_SET-FAIL] finish_reason={response.finish_reason}", flush=True)\n'
        '                print(f"[DEBUG TEST_SET-FAIL] status={response.status}", flush=True)\n'
        '                print(f"[DEBUG TEST_SET-FAIL] incomplete={response.incomplete_details}", flush=True)\n'
        '                print(f"[DEBUG TEST_SET-FAIL] usage={response.usage}", flush=True)\n'
        '                print(f"[DEBUG TEST_SET-FAIL] text_len={len(response.text or \'\')}", flush=True)\n'
        '                print(f"[DEBUG TEST_SET-FAIL] text[:1500]={(response.text or \'\')[:1500]!r}", flush=True)\n'
        '                print(f"[DEBUG TEST_SET-FAIL] parsed_type={type(payload).__name__}", flush=True)\n'
        '                raise ValueError(f"{kind} test-case generation returned invalid test_set payload")\n'
    )
    target.write_text(text.replace(sentinel, replacement, 1), encoding="utf-8")
    log.info("applied test_set diagnostic to %s", target)


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
    test_set_key = "test_set" if "test_set" in pipeline or "seeds" not in pipeline else "seeds"
    systematize_key = "systematize" if "systematize" in pipeline or "policy" not in pipeline else "policy"
    inference_key = "inference" if "inference" in pipeline or "rollout" not in pipeline else "rollout"

    # Sample sizes
    test_set_cfg = pipeline.setdefault(test_set_key, {})
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
    pipeline.setdefault(systematize_key, {}).setdefault("model", {})["name"] = upstream_model
    prompt_model = test_set_cfg.setdefault("prompt", {}).setdefault("model", {})
    prompt_model["name"] = upstream_model
    prompt_model["max_tokens"] = 16000
    scenario_model = test_set_cfg.setdefault("scenario", {}).setdefault("model", {})
    scenario_model["name"] = upstream_model
    scenario_model["max_tokens"] = 16000
    inference = pipeline.setdefault(inference_key, {})
    tester_key = "tester" if inference_key == "inference" or "auditor" not in inference else "auditor"
    inference.setdefault(tester_key, {}).setdefault("model", {})["name"] = upstream_model
    # Bump inference concurrency so test_set=200 finishes in workflow timeout.
    # qualevalexpeus has generous Azure quota for these deployments.
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
    extra_overrides: dict[str, Any] | None = None,
) -> Path:
    """Run ``p2m run`` against one config from a worktree at ``commit_sha``.

    Pipeline outputs land in ``<worktree>/artifacts/results/<suite>/<run>/``;
    we copy the run dir to ``REPO_ROOT/artifacts/regression-runs/`` so they
    survive worktree teardown and the workflow's actions/cache step can
    persist them across PR runs.
    """
    suite_dir = _suite_dir_for(config, commit_sha, test_set_size, judge_model)
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
        test_set_size=test_set_size,
        judge_model=judge_model,
        upstream_model=upstream_model,
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
        f"alpha (per-test) = {report['alpha']}, test_set_size = {report.get('test_set_size')}"
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
                test_set_size=args.test_set,
                judge_model=args.judge_model,
                upstream_model=args.upstream_model,
            )
            worktrees_created.add(args.treatment)
            treatment_dir = run_pipeline(
                config,
                commit_sha=args.treatment,
                test_set_size=args.test_set,
                judge_model=args.judge_model,
                upstream_model=args.upstream_model,
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
        test_set_size=args.test_set * len(args.configs),
    )
    report["per_config"] = per_config_results
    report["baseline_sha"] = args.baseline
    report["treatment_sha"] = args.treatment
    report["upstream_stages_rerun"] = rerun_upstream
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
