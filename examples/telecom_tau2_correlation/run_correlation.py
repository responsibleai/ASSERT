#!/usr/bin/env python3
"""
Orchestration script for the telecom τ²-bench ↔ p2m correlation study.

Runs tau2 and/or p2m evaluations across a set of models, then computes
Spearman rank correlation between their scores.

Usage:
    # All three stages with default models
    python run_correlation.py

    # Only tau2 + correlate (reuse existing p2m results)
    python run_correlation.py --stages tau2,correlate

    # Only p2m for a custom model list
    python run_correlation.py --stages p2m --models azure/gpt-4o-mini azure/gpt-4o

    # Dry-run to see what would execute
    python run_correlation.py --dry-run
"""
from __future__ import annotations

import argparse
import copy
import json
import logging
import shutil
import subprocess
import sys
import time
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# ── Defaults ────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]  # adaptive-eval root

DEFAULT_MODELS = [
    "azure/gpt-4o-mini",      # weak
    "azure/gpt-4o",           # medium
    "azure/gpt-5.4-nano",     # medium
    "azure/gpt-5.4-mini",     # medium-strong
    "azure/gpt-5.4",          # strong
    "azure/claude-sonnet-4",   # strong
    "azure/claude-opus-4",     # very strong
]

P2M_CONFIG = SCRIPT_DIR / "eval_config.yaml"
RESULTS_DIR = SCRIPT_DIR / "results"

# tau2 defaults
TAU2_DOMAIN = "telecom"
TAU2_USER_LLM = "azure/gpt-5.4-nano"
TAU2_NUM_TRIALS = 4
TAU2_MAX_CONCURRENCY = 5

# Cost estimation benchmarks (observed from gpt-5.4-nano, telecom domain).
# These are rough lower bounds; actual cost scales with model pricing.
_EST_TAU2_MINUTES_PER_MODEL = 8       # wall-clock with concurrency=5
_EST_P2M_MINUTES_PER_MODEL = 8        # 70 seeds
_EST_TAU2_COST_PER_MODEL_NANO = 4.50  # USD, agent+user at nano pricing
_EST_P2M_INPUT_TOKENS_PER_MODEL = 3_200_000


# ── Helpers ─────────────────────────────────────────────────────────
def model_slug(model: str) -> str:
    """Convert 'azure/gpt-4o-mini' → 'gpt-4o-mini'."""
    return model.rsplit("/", 1)[-1]


def run_cmd(cmd: list[str], *, dry_run: bool = False) -> subprocess.CompletedProcess | None:
    """Run a subprocess with output streamed to the terminal."""
    display = " ".join(cmd)
    if dry_run:
        logger.info("[DRY-RUN] %s", display)
        return None
    logger.info("Running: %s", display)
    t0 = time.monotonic()
    result = subprocess.run(cmd)
    elapsed = time.monotonic() - t0
    if result.returncode != 0:
        logger.error("Command failed (exit %d) after %.1fs", result.returncode, elapsed)
    else:
        logger.info("Completed in %.1fs (%.1f min)", elapsed, elapsed / 60)
    return result


# ── Stage: tau2 ─────────────────────────────────────────────────────
def run_tau2(models: list[str], *, dry_run: bool = False) -> dict[str, Path]:
    """Run tau2-bench on the telecom domain for each model.

    Returns a dict mapping model name → output JSON path.
    """
    outputs: dict[str, Path] = {}
    total = len(models)
    stage_t0 = time.monotonic()
    for i, model in enumerate(models, 1):
        slug = model_slug(model)
        logger.info("── tau2 model %d/%d: %s ──", i, total, model)
        save_name = f"telecom_{slug}"
        tau2_bin = shutil.which("tau2") or str(Path(sys.executable).parent / "tau2")
        cmd = [
            tau2_bin, "run",
            "--domain", TAU2_DOMAIN,
            "--agent-llm", model,
            "--user-llm", TAU2_USER_LLM,
            "--num-trials", str(TAU2_NUM_TRIALS),
            "--max-concurrency", str(TAU2_MAX_CONCURRENCY),
            "--save-to", save_name,
        ]
        result = run_cmd(cmd, dry_run=dry_run)
        # tau2 saves to {DATA_DIR}/simulations/<save_name>.json where DATA_DIR
        # is the tau2 package's data directory (typically <tau3-bench>/data/).
        try:
            from tau2.utils.utils import DATA_DIR
            output_path = Path(DATA_DIR) / "simulations" / f"{save_name}.json"
        except ImportError:
            # Fallback: assume tau2 data dir is relative to cwd
            output_path = Path("data") / "simulations" / f"{save_name}.json"
        outputs[model] = output_path
        if result and result.returncode != 0:
            logger.warning("tau2 failed for %s, skipping", model)
    if not dry_run:
        logger.info("── tau2 stage done: %.1f min for %d model(s) ──",
                     (time.monotonic() - stage_t0) / 60, total)
    return outputs


def collect_tau2_rewards(outputs: dict[str, Path]) -> dict[str, float]:
    """Parse tau2 output JSONs and return mean reward per model."""
    rewards: dict[str, float] = {}
    for model, path in outputs.items():
        if not path.exists():
            logger.warning("tau2 output not found: %s", path)
            continue
        data = json.loads(path.read_text())
        sims = data.get("simulations", [])
        if not sims:
            logger.warning("No simulations in %s", path)
            continue
        task_rewards = [s["reward_info"]["reward"] for s in sims]
        mean_reward = sum(task_rewards) / len(task_rewards)
        rewards[model] = mean_reward
        logger.info("tau2 %s: %.4f (n=%d)", model, mean_reward, len(task_rewards))
    return rewards


# ── Stage: p2m ──────────────────────────────────────────────────────
def run_p2m(models: list[str], *, dry_run: bool = False) -> dict[str, str]:
    """Run p2m evaluation for each model.

    Generates a temporary config per model with the target model overridden.
    Returns a dict mapping model name → run name (for results lookup).
    """
    base_config = yaml.safe_load(P2M_CONFIG.read_text())
    runs: dict[str, str] = {}
    total = len(models)
    stage_t0 = time.monotonic()

    for i, model in enumerate(models, 1):
        slug = model_slug(model)
        run_name = f"{slug}-eval"
        logger.info("── p2m model %d/%d: %s ──", i, total, model)

        # Deep-copy and patch the config for this model
        config = copy.deepcopy(base_config)
        config["run"] = run_name
        config["pipeline"]["rollout"]["target"]["model"]["name"] = model

        # Write temporary config next to the source config so that
        # relative paths (concept markdown, tool files) resolve correctly.
        tmp_config = P2M_CONFIG.parent / f"config_{slug}.yaml"
        tmp_config.parent.mkdir(parents=True, exist_ok=True)
        tmp_config.write_text(yaml.dump(config, default_flow_style=False, sort_keys=False))

        cmd = [
            shutil.which("p2m") or str(Path(sys.executable).parent / "p2m"),
            "run", "--config", str(tmp_config),
        ]
        result = run_cmd(cmd, dry_run=dry_run)
        runs[model] = run_name

        if result and result.returncode != 0:
            logger.warning("p2m failed for %s, skipping", model)

    if not dry_run:
        logger.info("── p2m stage done: %.1f min for %d model(s) ──",
                     (time.monotonic() - stage_t0) / 60, total)
    return runs


def collect_p2m_scores(suite_name: str, runs: dict[str, str]) -> dict[str, dict[str, float]]:
    """Collect p2m scores from artifacts.

    Returns {model: {dimension: violation_rate}} where violation_rate
    is the fraction of seeds where the dimension was flagged true.
    """
    scores: dict[str, dict[str, float]] = {}
    artifacts_base = REPO_ROOT / "artifacts" / "results" / suite_name

    for model, run_name in runs.items():
        scores_path = artifacts_base / run_name / "scores.jsonl"
        if not scores_path.exists():
            logger.warning("p2m scores not found: %s", scores_path)
            continue

        lines = scores_path.read_text().strip().splitlines()
        if not lines:
            logger.warning("Empty scores file: %s", scores_path)
            continue

        # Accumulate per-dimension verdicts
        dim_totals: dict[str, list[bool]] = {}
        for line in lines:
            record = json.loads(line)
            # p2m nests dimensions under verdict.dimensions
            verdicts = (
                record.get("verdict", {}).get("dimensions")
                or record.get("verdicts")
                or record.get("scores", {})
            )
            for dim, verdict in verdicts.items():
                dim_totals.setdefault(dim, []).append(bool(verdict))

        # Compute violation rate per dimension
        model_scores: dict[str, float] = {}
        for dim, vals in dim_totals.items():
            model_scores[dim] = sum(vals) / len(vals)

        # Overall score: 1 - mean violation rate (higher = better)
        if model_scores:
            mean_violation = sum(model_scores.values()) / len(model_scores)
            model_scores["_overall"] = 1.0 - mean_violation

        scores[model] = model_scores
        logger.info("p2m %s: overall=%.4f dims=%s", model,
                     model_scores.get("_overall", 0), model_scores)

    return scores


# ── Stage: correlate ────────────────────────────────────────────────
def compute_correlation(
    tau2_rewards: dict[str, float],
    p2m_scores: dict[str, dict[str, float]],
) -> dict[str, float]:
    """Compute Spearman rank correlation between tau2 and p2m scores.

    Returns {dimension: rho, ...} including _overall.
    """
    try:
        from scipy.stats import spearmanr
    except ImportError:
        logger.error("scipy is required for correlation. Install: pip install scipy")
        sys.exit(1)

    # Only models present in both
    common = sorted(set(tau2_rewards) & {m for m in p2m_scores if p2m_scores[m]})
    if len(common) < 3:
        logger.error("Need ≥3 models with results in both benchmarks, got %d", len(common))
        return {}

    tau2_vals = [tau2_rewards[m] for m in common]

    # Get all dimensions from p2m
    all_dims = set()
    for model_scores in p2m_scores.values():
        all_dims.update(model_scores.keys())

    correlations: dict[str, float] = {}
    for dim in sorted(all_dims):
        p2m_vals = []
        tau2_matched = []
        for m in common:
            if dim in p2m_scores[m]:
                # For violation dimensions, invert so higher = better (like tau2)
                val = p2m_scores[m][dim]
                if dim != "_overall":
                    val = 1.0 - val  # convert violation rate to success rate
                p2m_vals.append(val)
                tau2_matched.append(tau2_rewards[m])

        if len(p2m_vals) < 3:
            continue

        rho, pval = spearmanr(tau2_matched, p2m_vals)
        correlations[dim] = rho
        logger.info("Spearman ρ [%s]: %.4f (p=%.4f, n=%d)", dim, rho, pval, len(p2m_vals))

    return correlations


def save_results(
    tau2_rewards: dict[str, float],
    p2m_scores: dict[str, dict[str, float]],
    correlations: dict[str, float],
) -> Path:
    """Save combined results to a JSON file."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    output = {
        "tau2_rewards": tau2_rewards,
        "p2m_scores": p2m_scores,
        "correlations": correlations,
        "models_compared": sorted(set(tau2_rewards) & set(p2m_scores)),
    }
    out_path = RESULTS_DIR / "correlation_results.json"
    out_path.write_text(json.dumps(output, indent=2))
    logger.info("Results saved to %s", out_path)
    return out_path


def print_summary(
    tau2_rewards: dict[str, float],
    p2m_scores: dict[str, dict[str, float]],
    correlations: dict[str, float],
) -> None:
    """Print a human-readable summary table."""
    common = sorted(set(tau2_rewards) & set(p2m_scores))
    if not common:
        print("No common models with results in both benchmarks.")
        return

    # Header
    print("\n" + "=" * 72)
    print("TELECOM τ²-bench ↔ p2m CORRELATION RESULTS")
    print("=" * 72)

    # Model scores table
    print(f"\n{'Model':<25} {'tau2 reward':>12} {'p2m overall':>12}")
    print("-" * 50)
    for m in common:
        tau2_r = tau2_rewards.get(m, float("nan"))
        p2m_o = p2m_scores.get(m, {}).get("_overall", float("nan"))
        print(f"{model_slug(m):<25} {tau2_r:>12.4f} {p2m_o:>12.4f}")

    # Correlation table
    if correlations:
        print(f"\n{'Dimension':<30} {'Spearman ρ':>12}")
        print("-" * 43)
        for dim in sorted(correlations):
            print(f"{dim:<30} {correlations[dim]:>12.4f}")

    print("=" * 72 + "\n")


# ── Cost / progress helpers ─────────────────────────────────────────
def print_tau2_cost_summary(outputs: dict[str, Path]) -> None:
    """Print cost summary from tau2 simulation results."""
    for model, path in outputs.items():
        if not path.exists():
            continue
        data = json.loads(path.read_text())
        sims = data.get("simulations", [])
        if not sims:
            continue
        total_agent = sum(s.get("agent_cost", 0) for s in sims)
        total_user = sum(s.get("user_cost", 0) for s in sims)
        rewards = [s["reward_info"]["reward"] for s in sims]
        mean_reward = sum(rewards) / len(rewards)
        logger.info(
            "tau2 %s: %d sims, reward=%.4f, cost=$%.2f (agent=$%.2f + user=$%.2f)",
            model_slug(model), len(sims), mean_reward,
            total_agent + total_user, total_agent, total_user,
        )


def print_p2m_cost_summary(suite_name: str, runs: dict[str, str]) -> None:
    """Print token usage summary from p2m metrics files."""
    artifacts_base = REPO_ROOT / "artifacts" / "results" / suite_name
    for model, run_name in runs.items():
        metrics_path = artifacts_base / run_name / "metrics.json"
        if not metrics_path.exists():
            continue
        metrics = json.loads(metrics_path.read_text())
        totals = metrics.get("totals", {})
        elapsed = metrics.get("elapsed_s", 0)
        input_tok = totals.get("input_tokens", 0)
        output_tok = totals.get("output_tokens", 0)
        cached = totals.get("cached_input_tokens", 0)
        calls = totals.get("calls", 0)
        cache_pct = (cached / input_tok * 100) if input_tok else 0
        logger.info(
            "p2m %s: %d calls, %.2fM input (%.0f%% cached), %.1fK output, %.1f min",
            model_slug(model), calls,
            input_tok / 1e6, cache_pct, output_tok / 1e3, elapsed / 60,
        )


def confirm_stage(stage: str, models: list[str], *, yes: bool = False) -> bool:
    """Show cost/time estimate and ask for confirmation before an expensive stage."""
    if yes:
        return True

    n = len(models)
    slugs = ", ".join(model_slug(m) for m in models)

    if stage == "tau2":
        est_min = _EST_TAU2_MINUTES_PER_MODEL * n
        est_cost = _EST_TAU2_COST_PER_MODEL_NANO * n
        print(f"\n{'─' * 60}")
        print(f"  Stage: tau2 ({TAU2_NUM_TRIALS} trials, concurrency {TAU2_MAX_CONCURRENCY})")
        print(f"  Models ({n}): {slugs}")
        print(f"  Estimated: ~{est_min} min, ~${est_cost:.0f}+ (nano pricing)")
        print(f"  Note: cost scales with model pricing (gpt-5.4 >> nano)")
        print(f"{'─' * 60}")
    elif stage == "p2m":
        est_min = _EST_P2M_MINUTES_PER_MODEL * n
        est_tok = _EST_P2M_INPUT_TOKENS_PER_MODEL * n
        print(f"\n{'─' * 60}")
        print(f"  Stage: p2m evaluation (70 seeds per model)")
        print(f"  Models ({n}): {slugs}")
        print(f"  Estimated: ~{est_min} min, ~{est_tok / 1e6:.0f}M input tokens")
        print(f"{'─' * 60}")
    else:
        return True  # correlate is compute-only, no API cost

    answer = input("  Proceed? [y/N] ").strip().lower()
    return answer in ("y", "yes")


# ── Main ────────────────────────────────────────────────────────────
def main() -> None:
    global TAU2_NUM_TRIALS, TAU2_USER_LLM

    parser = argparse.ArgumentParser(
        description="Run telecom τ²-bench ↔ p2m correlation study.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--stages",
        type=lambda s: s.split(","),
        default=["tau2", "p2m", "correlate"],
        help="Comma-separated stages to run (default: tau2,p2m,correlate)",
    )
    parser.add_argument(
        "--models",
        nargs="+",
        default=DEFAULT_MODELS,
        help="Models to evaluate (default: 7 models weak→strong)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print commands without executing",
    )
    parser.add_argument(
        "--tau2-trials",
        type=int,
        default=TAU2_NUM_TRIALS,
        help=f"Number of tau2 trials per task (default: {TAU2_NUM_TRIALS})",
    )
    parser.add_argument(
        "--tau2-user-llm",
        default=TAU2_USER_LLM,
        help=f"LLM for tau2 user simulator (default: {TAU2_USER_LLM})",
    )
    parser.add_argument(
        "--p2m-seed-count",
        type=int,
        default=None,
        help="Override p2m seed prompt sample_size (default: use config value)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Debug-level logging",
    )
    parser.add_argument(
        "-y", "--yes",
        action="store_true",
        help="Skip confirmation prompts (auto-approve all stages)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%H:%M:%S",
    )

    # Apply overrides to module-level defaults
    TAU2_NUM_TRIALS = args.tau2_trials
    TAU2_USER_LLM = args.tau2_user_llm

    stages = [s.strip() for s in args.stages]
    valid_stages = {"tau2", "p2m", "correlate"}
    for s in stages:
        if s not in valid_stages:
            parser.error(f"Unknown stage '{s}'. Valid: {', '.join(valid_stages)}")

    logger.info("Stages: %s | Models: %s", stages, [model_slug(m) for m in args.models])

    # ── Run stages ──────────────────────────────────────────────────
    tau2_rewards: dict[str, float] = {}
    p2m_scores: dict[str, dict[str, float]] = {}
    correlations: dict[str, float] = {}

    # Load any existing results for stages we're skipping
    existing_results = RESULTS_DIR / "correlation_results.json"
    if existing_results.exists():
        existing = json.loads(existing_results.read_text())
        if "tau2" not in stages:
            tau2_rewards = existing.get("tau2_rewards", {})
            logger.info("Loaded %d tau2 results from previous run", len(tau2_rewards))
        if "p2m" not in stages:
            p2m_scores = existing.get("p2m_scores", {})
            logger.info("Loaded %d p2m results from previous run", len(p2m_scores))

    if "tau2" in stages:
        logger.info("═══ STAGE: tau2 ═══")
        if not confirm_stage("tau2", args.models, yes=args.dry_run or args.yes):
            logger.info("Skipped tau2 stage.")
        else:
            outputs = run_tau2(args.models, dry_run=args.dry_run)
            if not args.dry_run:
                tau2_rewards = collect_tau2_rewards(outputs)
                print_tau2_cost_summary(outputs)

    if "p2m" in stages:
        logger.info("═══ STAGE: p2m ═══")
        suite_name = yaml.safe_load(P2M_CONFIG.read_text()).get("suite", "telecom-tau2-correlation-v1")
        if not confirm_stage("p2m", args.models, yes=args.dry_run or args.yes):
            logger.info("Skipped p2m stage.")
        else:
            runs = run_p2m(args.models, dry_run=args.dry_run)
            if not args.dry_run:
                p2m_scores = collect_p2m_scores(suite_name, runs)
                print_p2m_cost_summary(suite_name, runs)

    if "correlate" in stages:
        logger.info("═══ STAGE: correlate ═══")
        if not tau2_rewards or not p2m_scores:
            logger.error("Need both tau2 and p2m results. Run those stages first.")
            sys.exit(1)
        correlations = compute_correlation(tau2_rewards, p2m_scores)
        save_results(tau2_rewards, p2m_scores, correlations)
        print_summary(tau2_rewards, p2m_scores, correlations)

    logger.info("Done.")


if __name__ == "__main__":
    main()
