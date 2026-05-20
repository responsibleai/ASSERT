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
DEFAULT_CONCURRENCY = 10
DEFAULT_TAU2_RETRIES = 3

# Cost estimation benchmarks (observed from gpt-5.4-nano, telecom domain).
# These are rough lower bounds; actual cost scales with model pricing.
_EST_TAU2_MINUTES_PER_MODEL = 4       # wall-clock with concurrency=10
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

    # Validate that pipeline models have a usable endpoint.
    # If AZURE_API_BASE isn't set, the pipeline_endpoint must point to
    # an endpoint whose env vars *are* set.
    if not os.environ.get("AZURE_API_BASE"):
        pipeline_key = config.get("pipeline_endpoint")
        if not pipeline_key:
            missing.setdefault("AZURE_API_BASE", []).append("pipeline models")
        else:
            endpoints = config.get("endpoints", {})
            api_keys = config.get("api_keys", {})
            for var_map, label in [(endpoints, "endpoint"), (api_keys, "api_key")]:
                env_var = var_map.get(pipeline_key)
                if env_var and not os.environ.get(env_var):
                    missing.setdefault(env_var, []).append(f"pipeline ({label})")

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
    """Build a subprocess env dict with AZURE_API_BASE and AZURE_API_KEY set for the given model.

    Suitable for single-model tools (tau2) where only one endpoint is needed.
    For p2m (which uses multiple models across endpoints), use make_p2m_env().
    """
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


def make_p2m_env(config: dict, model_name: str) -> dict[str, str]:
    """Build env for p2m subprocess with per-model endpoint routing.

    Unlike make_model_env (which overrides AZURE_API_BASE globally), this
    keeps the default AZURE_API_BASE/AZURE_API_KEY for pipeline models
    (systematize, test_set, tester, judge) and routes only the target
    model to its specific endpoint via _P2M_MODEL_ROUTING + _p2m_shim.py.

    When AZURE_API_BASE is not set in the environment, falls back to the
    ``pipeline_endpoint`` defined in models.yaml so that pipeline models
    always have a usable default endpoint.
    """
    env = os.environ.copy()

    # Ensure AZURE_API_BASE is set for pipeline models.
    # If the environment already has it, use it.  Otherwise fall back to
    # the pipeline_endpoint declared in models.yaml.
    if not env.get("AZURE_API_BASE"):
        pipeline_key = config.get("pipeline_endpoint")
        if pipeline_key:
            endpoints = config.get("endpoints", {})
            api_keys = config.get("api_keys", {})
            base_var = endpoints.get(pipeline_key, "AZURE_API_BASE")
            key_var = api_keys.get(pipeline_key, "AZURE_API_KEY")
            base_val = os.environ.get(base_var)
            key_val = os.environ.get(key_var)
            if base_val:
                env["AZURE_API_BASE"] = base_val
            if key_val:
                env.setdefault("AZURE_API_KEY", key_val)

    # Check if the target model uses a non-default endpoint.
    entry = _model_entry(config, model_name)
    endpoint_key = entry.get("endpoint") if entry else None

    if endpoint_key and endpoint_key != "default":
        routing: dict[str, dict[str, str]] = {}
        route: dict[str, str] = {}
        url = resolve_endpoint_url(config, model_name)
        if url:
            route["api_base"] = url
        key_var = resolve_api_key_env_var(config, model_name)
        key_val = os.environ.get(key_var)
        if key_val:
            route["api_key"] = key_val
        if route:
            routing[model_name] = route
            env["_P2M_MODEL_ROUTING"] = json.dumps(routing)

    return env


# ── Helpers ─────────────────────────────────────────────────────────
def model_slug(model: str) -> str:
    """Convert 'azure/gpt-4o-mini' → 'gpt-4o-mini'."""
    return model.rsplit("/", 1)[-1]


_ERROR_LINE_RE = re.compile(r"Error running task (.+?), trial (\d+): (.+)")


def _filter_pty_output(master_fd: int, seen_warnings: set[str],
                       errors: list[str] | None = None,
                       *, auto_resume: bool = False) -> None:
    """Read PTY output, filter noise, and write the rest to real stdout.

    Uses a pseudo-TTY so the subprocess sees a real terminal (rich progress
    bars render normally) while we intercept the byte stream to drop noisy
    litellm/loguru messages.  Complete newline-terminated lines are checked
    against noise patterns.  Incomplete data (rich progress updates that use
    carriage-return / ANSI cursor control) is flushed after a short timeout
    so live progress feels instantaneous.

    When *auto_resume* is True, the filter auto-answers tau2's interactive
    "Do you want to resume the run? (y/n)" prompt with "y".

    When *errors* is provided, tau2 ``ERROR`` lines are collected into
    the list for post-run summarisation.
    """
    pending = b""
    last_was_suppressed = False

    def _check_auto_resume(text: str) -> bool:
        """If *text* contains a tau2 resume prompt, auto-answer 'y'."""
        if not auto_resume:
            return False
        if "resume the run?" in text:
            try:
                os.write(master_fd, b"y\n")
                logger.debug("Auto-answered resume prompt with 'y'")
            except OSError:
                pass
            return True
        return False

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
                # Collect tau2 error lines for the summary
                if errors is not None:
                    m = _ERROR_LINE_RE.search(line)
                    if m:
                        errors.append(line.strip())
                # Auto-answer resume prompts that arrive on a full line
                _check_auto_resume(line)
                # Drop unconditionally noisy lines
                if any(p.search(line) for p in _NOISE_PATTERNS):
                    last_was_suppressed = True
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
                        last_was_suppressed = True
                        break
                if not matched_warn:
                    # Collapse blank lines left behind by suppressed output
                    if not line.strip() and last_was_suppressed:
                        continue
                    last_was_suppressed = False
                    sys.stdout.buffer.write(line_bytes + b"\n")
                    sys.stdout.buffer.flush()
        elif pending:
            # No new data for 100 ms — flush pending bytes (likely a
            # rich progress update using \r / ANSI cursor escapes).
            # Check for resume prompts (they don't end with \n).
            _check_auto_resume(pending.decode("utf-8", errors="replace"))
            sys.stdout.buffer.write(pending)
            sys.stdout.buffer.flush()
            pending = b""

    # Flush anything left over
    if pending:
        line = pending.decode("utf-8", errors="replace")
        _check_auto_resume(line)
        if not any(p.search(line) for p in _NOISE_PATTERNS):
            sys.stdout.buffer.write(pending)
            sys.stdout.buffer.flush()


def run_cmd(cmd: list[str], *, dry_run: bool = False,
            env: dict[str, str] | None = None,
            auto_resume: bool = False,
            errors_out: list[str] | None = None,
            ) -> subprocess.CompletedProcess | None:
    """Run a subprocess through a pseudo-TTY, filtering noisy output.

    The PTY lets rich progress bars and panels render normally (the child
    process sees a real terminal) while we intercept the byte stream to
    drop litellm / loguru noise.

    When *auto_resume* is True, stdin is connected through the PTY and
    tau2's interactive "Do you want to resume?" prompt is auto-answered.

    When *errors_out* is provided, tau2 error lines are collected into
    the list for post-run analysis.
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

    popen_kwargs = dict(env=env, stdout=slave_fd, stderr=slave_fd)
    if auto_resume:
        # Connect stdin through the PTY so we can auto-answer prompts
        popen_kwargs["stdin"] = slave_fd
    proc = subprocess.Popen(cmd, **popen_kwargs)
    os.close(slave_fd)  # parent doesn't need the slave end

    seen: set[str] = set()
    collected_errors: list[str] = errors_out if errors_out is not None else []
    filter_thread = threading.Thread(
        target=_filter_pty_output,
        args=(master_fd, seen, collected_errors),
        kwargs={"auto_resume": auto_resume},
        daemon=True,
    )
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
def discover_tau2_results(models: list[str], *, expected_trials: int = 0) -> dict[str, Path]:
    """Find existing tau2 output files that contain complete simulations.

    When *expected_trials* > 0, the expected simulation count is derived
    from the file's task list (``n_tasks × expected_trials``).

    * **Complete** files (sim count matches expected) are returned so the
      model is skipped in the next run.
    * **Partial** files (fewer sims) are *kept* on disk so tau2 can
      resume them — they are simply excluded from the returned dict so
      the model remains in the pending list.
    * **Over-count** files (more sims than expected, e.g. accumulated
      from pre-resume runs) are removed as stale.
    """
    existing: dict[str, Path] = {}
    sim_dir = TAU2_DATA_DIR / "simulations"
    for model in models:
        path = sim_dir / f"telecom_{model_slug(model)}.json"
        if path.exists():
            try:
                data = json.loads(path.read_text())
                sims = data.get("simulations", [])
                n_sims = len(sims)
                if n_sims == 0:
                    logger.debug("Ignoring %s (0 simulations)", path.name)
                    continue
                if expected_trials > 0:
                    n_tasks = len(data.get("tasks", []))
                    expected_total = n_tasks * expected_trials if n_tasks else 0
                    if expected_total > 0 and n_sims > expected_total:
                        logger.warning(
                            "Removing stale %s: has %d sims, expected %d",
                            path.name, n_sims, expected_total,
                        )
                        path.unlink()
                        continue
                    if expected_total > 0 and n_sims < expected_total:
                        logger.info(
                            "Partial %s: %d/%d sims — tau2 will resume",
                            path.name, n_sims, expected_total,
                        )
                        continue  # keep file for resume, don't mark complete
                existing[model] = path
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
def _summarize_tau2_run(model: str, output_path: Path,
                        errors: list[str], expected_trials: int) -> None:
    """Log a human-readable summary after a tau2 run fails or finishes."""
    # Read whatever was saved to disk (tau2 saves per-task incrementally)
    completed = 0
    expected_total = 0
    if output_path.exists():
        try:
            data = json.loads(output_path.read_text())
            completed = len(data.get("simulations", []))
            n_tasks = len(data.get("tasks", []))
            expected_total = n_tasks * expected_trials
        except (json.JSONDecodeError, OSError):
            pass

    # Summarise task-level errors captured from stdout
    error_summary: dict[str, int] = {}
    failed_tasks: list[str] = []
    for line in errors:
        m = _ERROR_LINE_RE.search(line)
        if m:
            task_id, _trial, reason = m.group(1), m.group(2), m.group(3)
            failed_tasks.append(task_id)
            # Normalise common error reasons for grouping
            if "content or tool calls" in reason:
                key = "empty model response (no content or tool calls)"
            elif "rate limit" in reason.lower() or "429" in reason:
                key = "rate-limited (429)"
            elif "timeout" in reason.lower():
                key = "timeout"
            else:
                key = reason[:80]
            error_summary[key] = error_summary.get(key, 0) + 1

    parts = [f"tau2 failed for {model}:"]
    if expected_total:
        parts.append(f"  completed {completed}/{expected_total} sims")
    if error_summary:
        parts.append("  errors:")
        for reason, count in sorted(error_summary.items(), key=lambda x: -x[1]):
            parts.append(f"    {count}× {reason}")
        unique_tasks = len(set(failed_tasks))
        parts.append(f"  {unique_tasks} unique task(s) had errors")
    else:
        parts.append("  (no parseable error lines captured)")
    if completed > 0:
        parts.append(f"  partial results saved — will resume on next run")
    logger.warning("\n".join(parts))


def _tau2_sim_count(output_path: Path) -> tuple[int, int]:
    """Return (n_completed, n_expected) for a tau2 simulation file.

    Returns (0, 0) if the file doesn't exist or is unreadable.
    """
    if not output_path.exists():
        return 0, 0
    try:
        data = json.loads(output_path.read_text())
        n_sims = len(data.get("simulations", []))
        n_tasks = len(data.get("tasks", []))
        return n_sims, n_tasks
    except (json.JSONDecodeError, OSError):
        return 0, 0


def run_tau2(models: list[str], models_config: dict, *, dry_run: bool = False,
             trials: int = DEFAULT_TRIALS, user_model: str = DEFAULT_USER_MODEL,
             concurrency: int = DEFAULT_CONCURRENCY,
             max_retries: int = DEFAULT_TAU2_RETRIES) -> dict[str, Path]:
    """Run tau2-bench on the telecom domain for each model.

    Crashed models are retried up to *max_retries* times.  Each retry
    resumes from partial results saved to disk by tau2.

    Returns a dict mapping model name → output JSON path.
    """
    outputs: dict[str, Path] = {}
    total = len(models)
    stage_t0 = time.monotonic()
    tau2_bin = shutil.which("tau2") or str(Path(sys.executable).parent / "tau2")
    if not Path(tau2_bin).exists() and not shutil.which("tau2"):
        logger.error(
            "tau2 CLI not found. Install it with:\n"
            "  pip install 'tau2 @ git+https://github.com/SEACrowd/tau3-bench.git'"
        )
        sys.exit(1)

    for i, model in enumerate(models, 1):
        slug = model_slug(model)
        save_name = f"telecom_{slug}"
        output_path = TAU2_DATA_DIR / "simulations" / f"{save_name}.json"
        outputs[model] = output_path

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

        expected_total = 0  # will be set after first run
        for attempt in range(1, max_retries + 1):
            attempt_label = f" (attempt {attempt}/{max_retries})" if max_retries > 1 else ""
            logger.info("── tau2 %s: %s%s ──",
                        _progress_line(i, total, stage_t0), model, attempt_label)

            errors: list[str] = []
            result = run_cmd(cmd, dry_run=dry_run, env=env,
                             auto_resume=True, errors_out=errors)
            if dry_run:
                break

            n_completed, n_tasks = _tau2_sim_count(output_path)
            expected_total = n_tasks * trials if n_tasks else expected_total

            if result and result.returncode == 0:
                logger.info("tau2 %s: completed successfully (%d sims)",
                            slug, n_completed)
                break  # success

            # Crashed — summarize and decide whether to retry
            _summarize_tau2_run(model, output_path, errors, trials)

            if expected_total and n_completed >= expected_total:
                logger.info("tau2 %s: all %d sims present despite exit code",
                            slug, n_completed)
                break  # all sims done even though process exited non-zero

            if attempt < max_retries:
                logger.info(
                    "tau2 %s: %d/%d sims — retrying (tau2 will resume from partial)",
                    slug, n_completed, expected_total or "?",
                )
            else:
                logger.warning(
                    "tau2 %s: %d/%d sims after %d attempt(s) — moving on",
                    slug, n_completed, expected_total or 0, max_retries,
                )

    if not dry_run:
        logger.info("── tau2 stage done: %.1f min for %d model(s) ──",
                     (time.monotonic() - stage_t0) / 60, total)
    return outputs


def print_tau2_completion_table(
    outputs: dict[str, Path], trials: int,
) -> dict[str, tuple[int, int]]:
    """Print a completion table and return {model: (completed, expected)}.

    Called after the tau2 stage to make partial results visible.
    """
    completion: dict[str, tuple[int, int]] = {}
    for model, path in outputs.items():
        n_completed, n_tasks = _tau2_sim_count(path)
        expected = n_tasks * trials if n_tasks else 0
        completion[model] = (n_completed, expected)

    print(f"\n{'─' * 60}")
    print("  tau2 completion:")
    for model, (done, expected) in completion.items():
        slug = model_slug(model)
        if expected:
            pct = done / expected * 100
            flag = "✓" if pct >= 80 else "⚠"
            print(f"    {slug:<25} {done:>4}/{expected:<4} ({pct:5.1f}%) {flag}")
        else:
            print(f"    {slug:<25} {done:>4}/???  ⚠")
    print(f"{'─' * 60}\n")
    return completion


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
            sys.executable, str(SCRIPT_DIR / "_p2m_shim.py"),
            "run", "--config", str(tmp_config),
        ]
        env = make_p2m_env(models_config, model)
        logger.debug("AZURE_API_BASE=%s (default), routing=%s",
                      env.get("AZURE_API_BASE", "<unset>"),
                      env.get("_P2M_MODEL_ROUTING", "<none>"))
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
def _tau2_sample_sizes(tau2_rewards: dict[str, float]) -> dict[str, int]:
    """Return {model: n_simulations} from tau2 output files on disk."""
    sizes: dict[str, int] = {}
    for model in tau2_rewards:
        slug = model_slug(model)
        path = TAU2_DATA_DIR / "simulations" / f"telecom_{slug}.json"
        n, _ = _tau2_sim_count(path)
        sizes[model] = n
    return sizes


def compute_correlation(
    tau2_rewards: dict[str, float],
    p2m_scores: dict[str, dict[str, float]],
) -> dict[str, dict]:
    """Compute Spearman rank correlation between tau2 and p2m scores.

    Returns {dimension: {"rho": float, "pval": float, "n": int}, ...}
    including _overall.
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

    correlations: dict[str, dict] = {}
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
        correlations[dim] = {"rho": rho, "pval": pval, "n": len(p2m_vals)}
        logger.info("Spearman ρ [%s]: %.4f (p=%.4f, n=%d)", dim, rho, pval, len(p2m_vals))

    return correlations


def save_results(
    tau2_rewards: dict[str, float],
    p2m_scores: dict[str, dict[str, float]],
    correlations: dict[str, dict],
) -> Path:
    """Save combined results to a JSON file."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    tau2_samples = _tau2_sample_sizes(tau2_rewards)
    output = {
        "tau2_rewards": tau2_rewards,
        "tau2_sample_sizes": tau2_samples,
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
    correlations: dict[str, dict],
) -> None:
    """Print a human-readable summary table with sample sizes."""
    common = sorted(set(tau2_rewards) & set(p2m_scores))
    if not common:
        print("No common models with results in both benchmarks.")
        return

    tau2_samples = _tau2_sample_sizes(tau2_rewards)

    # Header
    print("\n" + "=" * 72)
    print("TELECOM τ²-bench ↔ p2m CORRELATION RESULTS")
    print("=" * 72)

    # Model scores table
    print(f"\n{'Model':<25} {'tau2 reward':>12} {'tau2 n':>7} {'p2m overall':>12}")
    print("-" * 57)
    for m in common:
        tau2_r = tau2_rewards.get(m, float("nan"))
        p2m_o = p2m_scores.get(m, {}).get("_overall", float("nan"))
        n_sims = tau2_samples.get(m, 0)
        print(f"{model_slug(m):<25} {tau2_r:>12.4f} {n_sims:>7} {p2m_o:>12.4f}")

    # Correlation table
    if correlations:
        print(f"\n{'Dimension':<30} {'Spearman ρ':>10} {'p-value':>10} {'n':>4}")
        print("-" * 55)
        for dim in sorted(correlations):
            c = correlations[dim]
            rho = c["rho"] if isinstance(c, dict) else c
            pval = c.get("pval", float("nan")) if isinstance(c, dict) else float("nan")
            n = c.get("n", 0) if isinstance(c, dict) else 0
            sig = "*" if pval < 0.05 else ""
            print(f"{dim:<30} {rho:>10.4f} {pval:>10.4f} {n:>4} {sig}")

    # Data reliability note
    low_sample = [m for m in common if tau2_samples.get(m, 0) < 50]
    if low_sample:
        print(f"\n⚠ Low tau2 sample count (<50 sims): "
              f"{', '.join(model_slug(m) for m in low_sample)}")
        print("  Correlation may be unreliable for these models.")
        print("  Consider re-running with: "
              f"--stages tau2 --models {' '.join(low_sample)}")

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
        "--tau2-retries",
        type=int,
        default=None,
        help=f"Max retry attempts per model when tau2 crashes "
             f"(default: {DEFAULT_TAU2_RETRIES})",
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
    # Suppress noisy OTEL/gRPC export errors when Phoenix isn't running
    for _otel_logger in (
        "opentelemetry.exporter.otlp.proto.grpc.exporter",
        "opentelemetry.exporter.otlp.proto.grpc",
    ):
        logging.getLogger(_otel_logger).setLevel(logging.CRITICAL)

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
    tau2_retries = args.tau2_retries or DEFAULT_TAU2_RETRIES
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
        existing_tau2 = {} if skip_discovery else discover_tau2_results(
            models, expected_trials=trials,
        )
        pending = plan_and_confirm(
            "tau2", models, existing=existing_tau2,
            yes=args.dry_run or args.yes, trials=trials, concurrency=concurrency,
        )
        if pending:
            new_outputs = run_tau2(pending, models_config, dry_run=args.dry_run,
                                   trials=trials, user_model=user_model,
                                   concurrency=concurrency,
                                   max_retries=tau2_retries)
            if not args.dry_run:
                all_outputs = {**existing_tau2, **new_outputs}
                print_tau2_completion_table(all_outputs, trials)
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
