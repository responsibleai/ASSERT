"""Build the system message for the design-agent conversation.

Assembles the base prompt template with injected sections: schema
reference, preset catalog, seed config, CLI hints, and user description.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from p2m.core.io import load_prompt_text
from p2m.library.loader import discover, load_preset

log = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────

_PROMPT_FILENAME = "init_system.md"
_CONFIG_REF_PATH = Path(__file__).resolve().parents[2] / "CONFIG_REFERENCE.md"

# Token budget thresholds (fraction of model context window).
_WARN_THRESHOLD = 0.50
_TRIM_THRESHOLD = 0.80

# Rough chars-per-token estimate for budget checks.
_CHARS_PER_TOKEN = 4

# Default context window sizes (tokens) for well-known model families.
_DEFAULT_CONTEXT_WINDOWS: dict[str, int] = {
    "gpt-4.1-mini": 1_000_000,
    "gpt-4.1": 1_000_000,
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "claude-3-5-sonnet": 200_000,
    "claude-sonnet-4": 200_000,
}
_FALLBACK_CONTEXT_WINDOW = 128_000


def _estimate_tokens(text: str) -> int:
    """Rough token count from character length."""
    return max(1, len(text) // _CHARS_PER_TOKEN)


def _context_window_for(model: str) -> int:
    """Best-effort context window lookup."""
    for prefix, size in _DEFAULT_CONTEXT_WINDOWS.items():
        if model.startswith(prefix):
            return size
    return _FALLBACK_CONTEXT_WINDOW


# ── Section builders ───────────────────────────────────────────

def _build_schema_reference() -> str:
    """Load CONFIG_REFERENCE.md as the schema section."""
    if not _CONFIG_REF_PATH.is_file():
        log.warning("CONFIG_REFERENCE.md not found at %s", _CONFIG_REF_PATH)
        return ""
    return _CONFIG_REF_PATH.read_text(encoding="utf-8")


def _build_preset_catalog() -> str:
    """Format the available behavior and judge presets."""
    lines: list[str] = []
    for kind_label, kind_key in [("Behavior", "behavior"), ("Judge", "judge_preset")]:
        presets = discover(kind=kind_key)
        if not presets:
            continue
        lines.append(f"### {kind_label} Presets")
        for p in presets:
            name = p.get("name", "unknown")
            desc = p.get("description") or p.get("summary", "")
            lines.append(f"- **{name}**: {desc}")
        lines.append("")
    return "\n".join(lines)


def _build_seed_section(seed_path: Path | None) -> str:
    """Include the seed config YAML if provided via --from."""
    if seed_path is None:
        return ""
    if not seed_path.is_file():
        log.warning("Seed config not found: %s", seed_path)
        return ""
    content = seed_path.read_text(encoding="utf-8")
    return (
        "## Seed Configuration (from --from)\n\n"
        "The user wants to extend or modify this existing config:\n\n"
        f"```yaml\n{content}\n```\n"
    )


def _build_behavior_section(behavior_name: str | None) -> str:
    """Include the full behavior preset if --behavior was given."""
    if not behavior_name:
        return ""
    try:
        preset = load_preset("behavior", behavior_name)
    except ValueError as exc:
        log.warning("Could not load behavior preset: %s", exc)
        return ""
    dumped = yaml.dump(preset, default_flow_style=False, sort_keys=False)
    return (
        f"## Selected Behavior Preset: {behavior_name}\n\n"
        f"```yaml\n{dumped}```\n"
    )


def _build_judge_section(judge_name: str | None) -> str:
    """Include the judge preset if --judge-preset was given."""
    if not judge_name:
        return ""
    try:
        preset = load_preset("judge_preset", judge_name)
    except ValueError as exc:
        log.warning("Could not load judge preset: %s", exc)
        return ""
    dumped = yaml.dump(preset, default_flow_style=False, sort_keys=False)
    return (
        f"## Selected Judge Preset: {judge_name}\n\n"
        f"```yaml\n{dumped}```\n"
    )


def _build_dimension_hints(dimensions: list[str] | None) -> str:
    """Format dimension hints from --dimensions."""
    if not dimensions:
        return ""
    items = "\n".join(f"- {d}" for d in dimensions)
    return (
        "## Dimension Hints (from --dimensions)\n\n"
        "The user wants these variation axes included:\n\n"
        f"{items}\n"
    )


def _build_description_section(describe: str | None) -> str:
    """Include the one-line system description from --describe."""
    if not describe:
        return ""
    return (
        "## System Description (from --describe)\n\n"
        f"{describe}\n"
    )


# ── Public API ─────────────────────────────────────────────────

def build_system_message(
    *,
    seed_path: Path | None = None,
    behavior: str | None = None,
    judge_preset: str | None = None,
    dimensions: list[str] | None = None,
    describe: str | None = None,
    model: str = "gpt-4.1-mini",
) -> str:
    """Assemble the full system message for the design agent.

    Loads the prompt template from ``prompts/init_system.md`` and appends
    contextual sections based on CLI flags.  Applies token-budget
    trimming when the assembled prompt is too large for the model.
    """
    template = load_prompt_text(_PROMPT_FILENAME)

    # Build optional sections.
    sections = [
        _build_schema_reference(),
        _build_preset_catalog(),
        _build_seed_section(seed_path),
        _build_behavior_section(behavior),
        _build_judge_section(judge_preset),
        _build_dimension_hints(dimensions),
        _build_description_section(describe),
    ]

    full_prompt = template + "\n\n" + "\n\n".join(s for s in sections if s)

    # Budget check.
    ctx_window = _context_window_for(model)
    estimated = _estimate_tokens(full_prompt)

    if estimated > ctx_window * _TRIM_THRESHOLD:
        # Trim: drop schema reference and preset catalog (the two largest
        # optional sections) and keep only user-specific context.
        log.warning(
            "System prompt (~%d tokens) exceeds 80%% of %s context window "
            "(%d tokens). Trimming schema reference and preset catalog.",
            estimated, model, ctx_window,
        )
        trimmed_sections = [
            _build_seed_section(seed_path),
            _build_behavior_section(behavior),
            _build_judge_section(judge_preset),
            _build_dimension_hints(dimensions),
            _build_description_section(describe),
        ]
        full_prompt = template + "\n\n" + "\n\n".join(s for s in trimmed_sections if s)
    elif estimated > ctx_window * _WARN_THRESHOLD:
        log.warning(
            "System prompt (~%d tokens) exceeds 50%% of %s context window "
            "(%d tokens). Consider using a model with a larger context window.",
            estimated, model, ctx_window,
        )

    return full_prompt
