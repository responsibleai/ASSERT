#!/usr/bin/env python3
"""
Orchestration script for the telecom τ²-bench ↔ p2m comparison study.

Runs tau2 and/or p2m evaluations across a set of models, then computes
Spearman rank correlation between their scores.

Usage:
    # Quick preset (4 models, reduced test cases)
    python run_comparison.py --preset quick

    # Full preset (all models, full trial count)
    python run_comparison.py --preset full

    # Custom: specific stages and models
    python run_comparison.py --stages tau2,correlate --models azure/gpt-5.4-mini azure/grok-4

    # Dry-run to see what would execute
    python run_comparison.py --preset quick --dry-run
"""
from __future__ import annotations

import argparse
import copy
import json
import logging
import os
import pty
import re
import select
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()  # pick up .env from repo root

logger = logging.getLogger(__name__)

# Patterns for noisy subprocess stderr lines we want to suppress.
# The first match of _WARN_ONCE patterns is printed; subsequent duplicates are dropped.
_NOISE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"Give Feedback / Get Help"),
    re.compile(r"Provider List:"),
    re.compile(r"LiteLLM\.Info: If you need to debug"),
]
_WARN_ONCE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"This model isn't mapped yet"),
]

# ── Defaults ────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]  # adaptive-eval root

MODELS_CONFIG = SCRIPT_DIR / "models.yaml"
P2M_CONFIG = SCRIPT_DIR / "eval_config.yaml"
RESULTS_DIR = SCRIPT_DIR / "results"

# tau2 defaults
TAU2_DOMAIN = "telecom"
TAU2_DATA_DIR = Path(os.environ.get("TAU2_DATA_DIR", str(SCRIPT_DIR / "data")))
DEFAULT_USER_MODEL = "azure/gpt-5.4-mini"
DEFAULT_TRIALS = 4
DEFAULT_CONCURRENCY = 5

# Cost estimation benchmarks (observed from gpt-5.4-nano, telecom domain).
# These are rough lower bounds; actual cost scales with model pricing.
_EST_TAU2_MINUTES_PER_MODEL = 8       # wall-clock with concurrency=5
_EST_P2M_MINUTES_PER_MODEL = 8        # 70 test cases
_EST_TAU2_COST_PER_MODEL_NANO = 4.50  # USD, agent+user at nano pricing
_EST_P2M_INPUT_TOKENS_PER_MODEL = 3_200_000


# ── Config loading ──────────────────────────────────────────────────
def load_models_config() -> dict:
    """Load models.yaml and return the parsed config."""
    if not MODELS_CONFIG.exists():
        logger.error("models.yaml not found at %s", MODELS_CONFIG)
        sys.exit(1)
    return yaml.safe_load(MODELS_CONFIG.read_text())


def get_preset_models(config: dict, preset: str) -> list[str]:
    """Return model names included in a given preset."""
    return [
        m["name"] for m in config.get("models", [])
        if preset in m.get("presets", [])
    ]


def get_preset_overrides(config: dict, preset: str) -> dict:
    """Return the preset's parameter overrides (trials, concurrency, etc.)."""
    return config.get("presets", {}).get(preset, {})


# ── Endpoint resolution ─────────────────────────────────────────────
def _model_entry(config: dict, model_name: str) -> dict | None:
    """Look up a model's entry in models.yaml by name."""
    for m in config.get("models", []):
        if m["name"] == model_name:
            return m
    return None


def resolve_endpoint_url(config: dict, model_name: str) -> str | None:
    """Resolve a model's Azure endpoint URL from environment variables.

    Lookup chain: model.endpoint → endpoints.<key> → os.environ[env_var].
    Falls back to endpoints.default if the model has no endpoint field.
    Returns None if the env var is not set.
    """
    endpoints = config.get("endpoints", {})
    entry = _model_entry(config, model_name)
    endpoint_key = entry.get("endpoint") if entry else None
    env_var = endpoints.get(endpoint_key or "default", endpoints.get("default", "AZURE_API_BASE"))
    return os.environ.get(env_var)


def resolve_endpoint_env_var(config: dict, model_name: str) -> str:
    """Return the env var name for a model's endpoint (for diagnostics)."""
    endpoints = config.get("endpoints", {})
    entry = _model_entry(config, model_name)
    endpoint_key = entry.get("endpoint") if entry else None
    return endpoints.get(endpoint_key or "default", endpoints.get("default", "AZURE_API_BASE"))


def resolve_api_key_env_var(config: dict, model_name: str) -> str:
    """Return the env var name for a model's API key."""
    api_keys = config.get("api_keys", {})
    entry = _model_entry(config, model_name)
    endpoint_key = entry.get("endpoint") if entry else None
    return api_keys.get(endpoint_key or "default", api_keys.get("default", "AZURE_API_KEY"))


def resolve_user_model(config: dict, agent_model: str, fallback: str = DEFAULT_USER_MODEL) -> str:
    """Pick the user-simulator model for a given agent model.

    tau2 sets a single AZURE_API_BASE per subprocess, so the user simulator
    must be a deployment on the same endpoint as the agent model.
    Lookup chain: model.endpoint → user_simulator.<key> → fallback.
    """
    entry = _model_entry(config, agent_model)
    endpoint_key = entry.get("endpoint") if entry else None
    user_sim_map = config.get("user_simulator", {})
    return user_sim_map.get(endpoint_key or "default", fallback)


def validate_endpoints(config: dict, models: list[str]) -> None:
    """Verify all required endpoint and API key env vars are set. Exit if any are missing."""
    missing: dict[str, list[str]] = {}  # env_var → [models]
    for model in models:
        for resolve_fn in (resolve_endpoint_env_var, resolve_api_key_env_var):
            env_var = resolve_fn(config, model)
            if not os.environ.get(env_var):
                missing.setdefault(env_var, []).append(model_slug(model))
    if missing:
        logger.error("Missing environment variables:")
        for var, slugs in missing.items():
            logger.error("  %s  (needed by: %s)", var, ", ".join(slugs))
        sys.exit(1)


def validate_tau2_data() -> None:
    """Verify the tau2 data directory exists and has telecom domain files."""
    telecom_dir = TAU2_DATA_DIR / "tau2" / "domains" / "telecom"
    if not telecom_dir.is_dir():
        logger.error("tau2 data directory not found: %s", TAU2_DATA_DIR)
        logger.error("Clone the tau3-bench repo and symlink or copy its data/ directory:")
        logger.error("  git clone --depth 1 https://github.com/SEACrowd/tau3-bench.git /tmp/tau3-bench")
        logger.error("  ln -s /tmp/tau3-bench/data %s", SCRIPT_DIR / "data")
        logger.error("Or set TAU2_DATA_DIR to an existing tau3-bench data directory.")
        sys.exit(1)


def make_model_env(config: dict, model_name: str) -> dict[str, str]:
    """Build a subprocess env dict with AZURE_API_BASE and AZURE_API_KEY set for the given model."""
    env = os.environ.copy()
    url = resolve_endpoint_url(config, model_name)
    if url:
        env["AZURE_API_BASE"] = url
    key_var = resolve_api_key_env_var(config, model_name)
    key_val = os.environ.get(key_var)
    if key_val:
        env["AZURE_API_KEY"] = key_val
    # Point tau2 at local data directory (domain data + simulation outputs)
    env.setdefault("TAU2_DATA_DIR", str(TAU2_DATA_DIR))
    return env


# ── Helpers ─────────────────────────────────────────────────────────
def model_slug(model: str) -> str:
    """Convert 'azure/gpt-4o-mini' → 'gpt-4o-mini'."""
    return model.rsplit("/", 1)[-1]


def _filter_pty_output(master_fd: int, seen_warnings: set[str]) -> None:
    """Read PTY output, filter noise, and write the rest to real stdout.

    Uses a pseudo-TTY so the subprocess sees a real terminal (rich progress
    bars render normally) while we intercept the byte stream to drop noisy
    litellm/loguru messages.  Complete newline-terminated lines are checked
    against noise patterns.  Incomplete data (rich progress updates that use
    carriage-return / ANSI cursor control) is flushed after a short timeout
    so live progress feels instantaneous.
    """
    pending = b""
    while True:
        ready, _, _ = select.select([master_fd], [], [], 0.1)
        if ready:
            try:
                data = os.read(master_fd, 8192)
            except OSError:
                break
            if not data:
                break
            pending += data
            # Process all complete (newline-terminated) lines
            while b"\n" in pending:
                line_bytes, pending = pending.split(b"\n", 1)
                line = line_bytes.decode("utf-8", errors="replace")
                # Drop unconditionally noisy lines
                if any(p.search(line) for p in _NOISE_PATTERNS):
                    continue
                # Warn-once: show first occurrence only
                matched_warn = False
                for p in _WARN_ONCE_PATTERNS:
                    if p.search(line):
                        key = p.pattern
                        if key not in seen_warnings:
                            seen_warnings.add(key)
                            sys.stdout.buffer.write(line_bytes + b"\n")
                            sys.stdout.buffer.write(
                                b"  (further identical warnings suppressed)\n"
                            )
                            sys.stdout.buffer.flush()
                        matched_warn = True
                        break
                if not matched_warn:
                    sys.stdout.buffer.write(line_bytes + b"\n")
                    sys.stdout.buffer.flush()
        elif pending:
            # No new data for 100 ms — flush pending bytes (likely a
            # rich progress update using \r / ANSI cursor escapes).
            sys.stdout.buffer.write(pending)
            sys.stdout.buffer.flush()
            pending = b""

    # Flush anything left over
    if pending:
        line = pending.decode("utf-8", errors="replace")
        if not any(p.search(line) for p in _NOISE_PATTERNS):
            sys.stdout.buffer.write(pending)
            sys.stdout.buffer.flush()


def run_cmd(cmd: list[str], *, dry_run: bool = False,
            env: dict[str, str] | None = None) -> subprocess.CompletedProcess | None:
    """Run a subprocess through a pseudo-TTY, filtering noisy output.

    The PTY lets rich progress bars and panels render normally (the child
    process sees a real terminal) while we intercept the byte stream to
    drop litellm / loguru noise.
    """
    display = " ".join(cmd)
    if dry_run:
        logger.info("[DRY-RUN] %s", display)
        return None
    logger.info("Running: %s", display)
    t0 = time.monotonic()

    # Create a pseudo-TTY so the subprocess thinks it has a real terminal
    master_fd, slave_fd = pty.openpty()

    # Propagate the real terminal size so rich panels wrap correctly
    try:
        import fcntl
        import struct
        import termios
        if sys.stdout.isatty():
            size = fcntl.ioctl(sys.stdout.fileno(),
                               termios.TIOCGWINSZ, b"\x00" * 8)
            fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, size)
    except Exception:
        pass  # best-effort; default 80×24 is fine

    proc = subprocess.Popen(cmd, env=env,
                            stdout=slave_fd, stderr=slave_fd)
    os.close(slave_fd)  # parent doesn't need the slave end

    seen: set[str] = set()
    filter_thread = threading.Thread(target=_filter_pty_output,
                                     args=(master_fd, seen), daemon=True)
    filter_thread.start()
    proc.wait()
    filter_thread.join(timeout=5)
    try:
        os.close(master_fd)
    except OSError:
        pass
    elapsed = time.monotonic() - t0
    result = subprocess.CompletedProcess(cmd, proc.returncode)
    if result.returncode != 0:
        logger.error("Command failed (exit %d) after %.1fs", result.returncode, elapsed)
    else:
        logger.info("Completed in %.1fs (%.1f min)", elapsed, elapsed / 60)
    return result


# ── Result discovery ────────────────────────────────────────────────
def discover_tau2_results(models: list[str]) -> dict[str, Path]:
    """Find existing tau2 output files that contain actual simulations."""
    existing: dict[str, Path] = {}
    sim_dir = TAU2_DATA_DIR / "simulations"
    for model in models:
        path = sim_dir / f"telecom_{model_slug(model)}.json"
        if path.exists():
            try:
                data = json.loads(path.read_text())
                if data.get("simulations"):
                    existing[model] = path
                else:
                    logger.debug("Ignoring %s (0 simulations)", path.name)
            except (json.JSONDecodeError, OSError):
                logger.debug("Ignoring %s (unreadable)", path.name)
    return existing


def discover_p2m_results(suite_name: str, models: list[str]) -> dict[str, str]:
    """Find existing p2m score files for the given models."""
    existing: dict[str, str] = {}
    artifacts_base = REPO_ROOT / "artifacts" / "results" / suite_name
    for model in models:
        run_name = f"{model_slug(model)}-eval"
        if (artifacts_base / run_name / "scores.jsonl").exists():
            existing[model] = run_name
    return existing


def _progress_line(i: int, total: int, t0: float) -> str:
    """Return '[3/9] elapsed 24m, ETA ~48m' or just '[1/9]' for the first."""
    elapsed = time.monotonic() - t0
    if i > 1:
        per_model = elapsed / (i - 1)
        remaining = per_model * (total - i + 1)
        return f"[{i}/{total}] elapsed {elapsed / 60:.0f}m, ETA ~{remaining / 60:.0f}m"
    return f"[{i}/{total}]"


# ── Stage: tau2 ─────────────────────────────────────────────────────
def run_tau2(models: list[str], models_config: dict, *, dry_run: bool = False,
             trials: int = DEFAULT_TRIALS, user_model: str = DEFAULT_USER_MODEL,
             concurrency: int = DEFAULT_CONCURRENCY) -> dict[str, Path]:
    """Run tau2-bench on the telecom domain for each model.

    Returns a dict mapping model name → output JSON path.
    """
    outputs: dict[str, Path] = {}
    total = len(models)
    stage_t0 = time.monotonic()
    for i, model in enumerate(models, 1):
        slug = model_slug(model)
        logger.info("── tau2 %s: %s ──", _progress_line(i, total, stage_t0), model)
        save_name = f"telecom_{slug}"
        tau2_bin = shutil.which("tau2") or str(Path(sys.executable).parent / "tau2")
        if not Path(tau2_bin).exists() and not shutil.which("tau2"):
            logger.error(
                "tau2 CLI not found. Install it with:\n"
                "  pip install 'tau2 @ git+https://github.com/SEACrowd/tau3-bench.git'"
            )
            sys.exit(1)
        # Pick the user-sim model for the same endpoint as the agent model
        effective_user_model = resolve_user_model(models_config, model, fallback=user_model)
        cmd = [
            tau2_bin, "run",
            "--domain", TAU2_DOMAIN,
            "--agent-llm", model,
            "--user-llm", effective_user_model,
            "--num-trials", str(trials),
            "--max-concurrency", str(concurrency),
            "--save-to", save_name,
        ]
        env = make_model_env(models_config, model)
        logger.debug("AZURE_API_BASE=%s (via %s), user-llm=%s",
                      env.get("AZURE_API_BASE", "<unset>"),
                      resolve_endpoint_env_var(models_config, model),
                      effective_user_model)
        result = run_cmd(cmd, dry_run=dry_run, env=env)
        # tau2 saves to {TAU2_DATA_DIR}/simulations/<save_name>.json
        output_path = TAU2_DATA_DIR / "simulations" / f"{save_name}.json"
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
def run_p2m(
    models: list[str],
    models_config: dict,
    *,
    dry_run: bool = False,
    test_cases: int | None = None,
    max_turns: int | None = None,
    judge_model: str | None = None,
) -> dict[str, str]:
    """Run p2m evaluation for each model.

    Generates a temporary config per model with the target model overridden.
    Optional overrides patch config values before writing the temp file.
    Returns a dict mapping model name → run name (for results lookup).
    """
    base_config = yaml.safe_load(P2M_CONFIG.read_text())
    runs: dict[str, str] = {}
    total = len(models)
    stage_t0 = time.monotonic()

    for i, model in enumerate(models, 1):
        slug = model_slug(model)
        run_name = f"{slug}-eval"
        logger.info("── p2m %s: %s ──", _progress_line(i, total, stage_t0), model)

        # Deep-copy and patch the config for this model
        config = copy.deepcopy(base_config)
        config["run"] = run_name
        config["pipeline"]["inference"]["target"]["model"]["name"] = model

        # Apply optional overrides
        if test_cases is not None:
            config["pipeline"]["test_set"]["prompt"]["sample_size"] = test_cases
            config["pipeline"]["test_set"]["scenario"]["sample_size"] = max(1, test_cases // 3)
        if max_turns is not None:
            config["pipeline"]["inference"]["max_turns"] = max_turns
        if judge_model is not None:
            config["pipeline"]["judge"]["model"]["name"] = judge_model

        # Write temporary config next to the source config so that
        # relative paths (tool files) resolve correctly.
        tmp_config = P2M_CONFIG.parent / f"config_{slug}.yaml"
        tmp_config.parent.mkdir(parents=True, exist_ok=True)
        tmp_config.write_text(yaml.dump(config, default_flow_style=False, sort_keys=False))

        cmd = [
            shutil.which("p2m") or str(Path(sys.executable).parent / "p2m"),
            "run", "--config", str(tmp_config),
        ]
        env = make_model_env(models_config, model)
        logger.debug("AZURE_API_BASE=%s (via %s)", env.get("AZURE_API_BASE", "<unset>"),
                      resolve_endpoint_env_var(models_config, model))
        result = run_cmd(cmd, dry_run=dry_run, env=env)
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
    is the fraction of test cases where the dimension was flagged true.
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


def plan_and_confirm(stage: str, models: list[str], *,
                     existing: dict | None = None, yes: bool = False,
                     trials: int = DEFAULT_TRIALS,
                     concurrency: int = DEFAULT_CONCURRENCY) -> list[str]:
    """Show execution plan with discovered results and ask for confirmation.

    Returns the list of models to run (pending only). Empty if user
    declines or all models already have results.
    """
    existing = existing or {}
    pending = [m for m in models if m not in existing]

    print(f"\n{'─' * 60}")
    print(f"  Stage: {stage}")
    if existing:
        print(f"  Done ({len(existing)}): "
              f"{', '.join(model_slug(m) for m in existing)}")
    if not pending:
        print(f"  All {len(models)} model(s) already have results.")
        print(f"{'─' * 60}", flush=True)
        return []

    n = len(pending)
    slugs = ", ".join(model_slug(m) for m in pending)
    if stage == "tau2":
        est_min = _EST_TAU2_MINUTES_PER_MODEL * n
        est_cost = _EST_TAU2_COST_PER_MODEL_NANO * n
        print(f"  To run ({n}): {slugs}")
        print(f"  Params: {trials} trials, concurrency {concurrency}")
        print(f"  Estimated: ~{est_min} min, ~${est_cost:.0f}+ (nano pricing)")
        print(f"  Note: cost scales with model pricing (gpt-5.4 >> nano)")
    elif stage == "p2m":
        est_min = _EST_P2M_MINUTES_PER_MODEL * n
        est_tok = _EST_P2M_INPUT_TOKENS_PER_MODEL * n
        print(f"  To run ({n}): {slugs}")
        print(f"  Estimated: ~{est_min} min, ~{est_tok / 1e6:.0f}M input tokens")
    else:
        print(f"  To run ({n}): {slugs}")
    print(f"{'─' * 60}", flush=True)

    if yes:
        return pending
    answer = input("  Proceed? [y/N] ").strip().lower()
    return pending if answer in ("y", "yes") else []


# ── Main ────────────────────────────────────────────────────────────
def main() -> None:
    models_config = load_models_config()

    parser = argparse.ArgumentParser(
        description="Run telecom τ²-bench ↔ p2m comparison study.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--preset",
        choices=list(models_config.get("presets", {}).keys()),
        default=None,
        help="Named preset from models.yaml (sets models, trials, concurrency, etc.)",
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
        default=None,
        help="Models to evaluate (overrides preset selection)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print commands without executing",
    )
    parser.add_argument(
        "--trials",
        type=int,
        default=None,
        help=f"Number of tau2 trials per task (default: {DEFAULT_TRIALS})",
    )
    parser.add_argument(
        "--user-model",
        default=DEFAULT_USER_MODEL,
        help=f"Fallback LLM for tau2 user simulator when no per-endpoint "
             f"mapping exists in models.yaml (default: {DEFAULT_USER_MODEL})",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=None,
        help=f"Max concurrent tasks (default: {DEFAULT_CONCURRENCY})",
    )
    parser.add_argument(
        "--test-cases",
        type=int,
        default=None,
        help="Override p2m test_set prompt sample_size (default: use config value)",
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
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-run all models even if results already exist on disk",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%H:%M:%S",
    )

    # ── Resolve preset + CLI overrides ──────────────────────────────
    preset_overrides = {}
    if args.preset:
        preset_overrides = get_preset_overrides(models_config, args.preset)
        logger.info("Preset '%s': %s", args.preset, preset_overrides.get("description", ""))

    # Models: CLI --models > preset > all models in config
    if args.models:
        models = args.models
    elif args.preset:
        models = get_preset_models(models_config, args.preset)
    else:
        models = [m["name"] for m in models_config.get("models", [])]

    # Numeric params: CLI > preset > defaults
    trials = args.trials or preset_overrides.get("trials", DEFAULT_TRIALS)
    concurrency = args.concurrency or preset_overrides.get("concurrency", DEFAULT_CONCURRENCY)
    test_cases = args.test_cases or preset_overrides.get("test_cases")
    max_turns = preset_overrides.get("max_turns")
    judge_model = preset_overrides.get("judge_model")
    user_model = args.user_model

    stages = [s.strip() for s in args.stages]
    valid_stages = {"tau2", "p2m", "correlate"}
    for s in stages:
        if s not in valid_stages:
            parser.error(f"Unknown stage '{s}'. Valid: {', '.join(valid_stages)}")

    logger.info("Stages: %s | Models: %s", stages, [model_slug(m) for m in models])

    # ── Validate endpoints before expensive API calls ───────────────
    api_stages = {"tau2", "p2m"}
    if api_stages & set(stages) and not args.dry_run:
        validate_endpoints(models_config, models)

    # ── Validate tau2 data directory ────────────────────────────────
    if "tau2" in stages:
        validate_tau2_data()

    # ── Run stages ──────────────────────────────────────────────────
    suite_name = yaml.safe_load(P2M_CONFIG.read_text()).get("suite", "telecom-tau2-correlation")
    tau2_rewards: dict[str, float] = {}
    p2m_scores: dict[str, dict[str, float]] = {}
    correlations: dict[str, float] = {}

    # Load aggregate results for stages we're skipping entirely
    existing_results = RESULTS_DIR / "correlation_results.json"
    if existing_results.exists():
        saved = json.loads(existing_results.read_text())
        if "tau2" not in stages:
            tau2_rewards = saved.get("tau2_rewards", {})
            logger.info("Loaded %d tau2 results from previous run", len(tau2_rewards))
        if "p2m" not in stages:
            p2m_scores = saved.get("p2m_scores", {})
            logger.info("Loaded %d p2m results from previous run", len(p2m_scores))

    skip_discovery = args.dry_run or args.force

    if "tau2" in stages:
        logger.info("═══ STAGE: tau2 ═══")
        existing_tau2 = {} if skip_discovery else discover_tau2_results(models)
        pending = plan_and_confirm(
            "tau2", models, existing=existing_tau2,
            yes=args.dry_run or args.yes, trials=trials, concurrency=concurrency,
        )
        if pending:
            new_outputs = run_tau2(pending, models_config, dry_run=args.dry_run,
                                   trials=trials, user_model=user_model,
                                   concurrency=concurrency)
            if not args.dry_run:
                all_outputs = {**existing_tau2, **new_outputs}
                tau2_rewards = collect_tau2_rewards(all_outputs)
                print_tau2_cost_summary(new_outputs)
        elif existing_tau2:
            logger.info("All tau2 results exist, collecting scores.")
            tau2_rewards = collect_tau2_rewards(existing_tau2)
        else:
            logger.info("Skipped tau2 stage.")
        # Save intermediate so a later --stages=correlate can pick this up
        if tau2_rewards:
            RESULTS_DIR.mkdir(parents=True, exist_ok=True)
            (RESULTS_DIR / "tau2_rewards.json").write_text(
                json.dumps(tau2_rewards, indent=2) + "\n")

    if "p2m" in stages:
        logger.info("═══ STAGE: p2m ═══")
        existing_p2m = {} if skip_discovery else discover_p2m_results(suite_name, models)
        pending = plan_and_confirm(
            "p2m", models, existing=existing_p2m,
            yes=args.dry_run or args.yes,
        )
        if pending:
            new_runs = run_p2m(pending, models_config, dry_run=args.dry_run,
                               test_cases=test_cases, max_turns=max_turns,
                               judge_model=judge_model)
            if not args.dry_run:
                all_runs = {**existing_p2m, **new_runs}
                p2m_scores = collect_p2m_scores(suite_name, all_runs)
                print_p2m_cost_summary(suite_name, new_runs)
        elif existing_p2m:
            logger.info("All p2m results exist, collecting scores.")
            p2m_scores = collect_p2m_scores(suite_name, existing_p2m)
        else:
            logger.info("Skipped p2m stage.")
        # Save intermediate so a later --stages=correlate can pick this up
        if p2m_scores:
            RESULTS_DIR.mkdir(parents=True, exist_ok=True)
            (RESULTS_DIR / "p2m_scores.json").write_text(
                json.dumps(p2m_scores, indent=2) + "\n")

    if "correlate" in stages:
        logger.info("═══ STAGE: correlate ═══")
        # Load intermediates if not computed in this run
        if not tau2_rewards:
            f = RESULTS_DIR / "tau2_rewards.json"
            if f.exists():
                tau2_rewards = json.loads(f.read_text())
                logger.info("Loaded %d tau2 rewards from intermediate file", len(tau2_rewards))
        if not p2m_scores:
            f = RESULTS_DIR / "p2m_scores.json"
            if f.exists():
                p2m_scores = json.loads(f.read_text())
                logger.info("Loaded %d p2m scores from intermediate file", len(p2m_scores))
        if not tau2_rewards or not p2m_scores:
            logger.error("Need both tau2 and p2m results. Run those stages first.")
            sys.exit(1)
        correlations = compute_correlation(tau2_rewards, p2m_scores)
        save_results(tau2_rewards, p2m_scores, correlations)
        print_summary(tau2_rewards, p2m_scores, correlations)

    logger.info("Done.")


if __name__ == "__main__":
    main()
