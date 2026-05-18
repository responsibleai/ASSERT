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
import subprocess
import sys
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
TAU2_USER_LLM = "azure/gpt-5.4-mini"
TAU2_NUM_TRIALS = 4
TAU2_MAX_CONCURRENCY = 5


# ── Helpers ─────────────────────────────────────────────────────────
def model_slug(model: str) -> str:
    """Convert 'azure/gpt-4o-mini' → 'gpt-4o-mini'."""
    return model.rsplit("/", 1)[-1]


def run_cmd(cmd: list[str], *, dry_run: bool = False) -> subprocess.CompletedProcess | None:
    """Run a subprocess, logging the command."""
    display = " ".join(cmd)
    if dry_run:
        logger.info("[DRY-RUN] %s", display)
        return None
    logger.info("Running: %s", display)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error("Command failed (exit %d):\n%s", result.returncode, result.stderr)
    else:
        logger.debug("stdout:\n%s", result.stdout[:2000])
    return result


# ── Stage: tau2 ─────────────────────────────────────────────────────
def run_tau2(models: list[str], *, dry_run: bool = False) -> dict[str, Path]:
    """Run tau2-bench on the telecom domain for each model.

    Returns a dict mapping model name → output JSON path.
    """
    outputs: dict[str, Path] = {}
    for model in models:
        slug = model_slug(model)
        save_name = f"telecom_{slug}"
        cmd = [
            "tau2", "run",
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

    for model in models:
        slug = model_slug(model)
        run_name = f"{slug}-eval"

        # Deep-copy and patch the config for this model
        config = copy.deepcopy(base_config)
        config["run"] = run_name
        config["pipeline"]["rollout"]["target"]["model"]["name"] = model

        # Write temporary config
        tmp_config = RESULTS_DIR / f"config_{slug}.yaml"
        tmp_config.parent.mkdir(parents=True, exist_ok=True)
        tmp_config.write_text(yaml.dump(config, default_flow_style=False, sort_keys=False))

        cmd = ["p2m", "run", "--config", str(tmp_config)]
        result = run_cmd(cmd, dry_run=dry_run)
        runs[model] = run_name

        if result and result.returncode != 0:
            logger.warning("p2m failed for %s, skipping", model)

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
            verdicts = record.get("verdicts", record.get("scores", {}))
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


# ── Main ────────────────────────────────────────────────────────────
def main() -> None:
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
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%H:%M:%S",
    )

    # Apply overrides to module-level defaults
    global TAU2_NUM_TRIALS, TAU2_USER_LLM
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
        outputs = run_tau2(args.models, dry_run=args.dry_run)
        if not args.dry_run:
            tau2_rewards = collect_tau2_rewards(outputs)

    if "p2m" in stages:
        logger.info("═══ STAGE: p2m ═══")
        suite_name = yaml.safe_load(P2M_CONFIG.read_text()).get("suite", "telecom-tau2-correlation-v1")
        runs = run_p2m(args.models, dry_run=args.dry_run)
        if not args.dry_run:
            p2m_scores = collect_p2m_scores(suite_name, runs)

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
