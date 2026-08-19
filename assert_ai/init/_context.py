"""Build the system message for the design-agent conversation.

Assembles the base prompt template with injected sections: schema
reference, preset catalog, seed config, CLI hints, and user description.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from assert_ai.core.io import load_prompt_text
from assert_ai.core.yaml_io import dump_yaml
from assert_ai.library.loader import discover, load_preset

log = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────

_PROMPT_FILENAME = "init_system.md"
_CONFIG_REF_PATH = Path(__file__).resolve().parents[2] / "docs" / "config" / "schema.md"
_HARM_SKILL_PATH = (
    Path(__file__).resolve().parents[2]
    / ".github"
    / "skills"
    / "assert-add-harm-eval-template"
    / "SKILL.md"
)

# Token budget thresholds (fraction of model context window).
_WARN_THRESHOLD = 0.50
_TRIM_THRESHOLD = 0.80

# Rough chars-per-token estimate for budget checks.
_CHARS_PER_TOKEN = 4

# Default context window sizes (tokens) for well-known model families.
_DEFAULT_CONTEXT_WINDOWS: dict[str, int] = {
    "gpt-5.4-mini": 1_000_000,
    "gpt-5.4": 1_000_000,
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


# Public alias for use by other init modules.
estimate_tokens = _estimate_tokens


def _context_window_for(model: str) -> int:
    """Best-effort context window lookup."""
    # Strip provider prefixes like "azure/" so "azure/gpt-5.4-mini" matches "gpt-5.4-mini".
    bare = model.split("/", 1)[-1] if "/" in model else model
    for prefix, size in _DEFAULT_CONTEXT_WINDOWS.items():
        if bare.startswith(prefix):
            return size
    return _FALLBACK_CONTEXT_WINDOW


# Public alias for use by other init modules.
context_window_for = _context_window_for


# ── Section builders ───────────────────────────────────────────

def _build_schema_reference() -> str:
    """Load docs/config/schema.md as the schema section."""
    if not _CONFIG_REF_PATH.is_file():
        log.warning("docs/config/schema.md not found at %s", _CONFIG_REF_PATH)
        return ""
    return _CONFIG_REF_PATH.read_text(encoding="utf-8")


def _build_harm_skill_section(web_search: bool) -> str:
    """Load the harm-eval-template skill for the automatic init flow.

    The design agent injects this so that, when the user picks the
    *Automatic harm-template flow*, the LLM can follow the same
    methodology as the standalone ``assert-add-harm-eval-template``
    skill. The skill file is the single source of truth; here we wrap it
    with an adaptation preamble that reconciles the skill's agent-oriented
    steps (research, writing files) with the ``assert-ai init`` runtime,
    which drives a single LLM via an ask/propose/done protocol. The
    research bullet flips based on ``web_search``: with live search on the
    model runs the skill's research loop for real and cites retrieved
    pages; with it off the model grounds in framework knowledge and does
    not fabricate URLs.
    """
    if not _HARM_SKILL_PATH.is_file():
        log.warning("Harm eval template skill not found at %s", _HARM_SKILL_PATH)
        return ""
    skill_text = _HARM_SKILL_PATH.read_text(encoding="utf-8")
    if web_search:
        research_bullet = (
            "- You have a live `web_search` tool this session (OpenAI/Azure "
            "Responses API `web_search_preview`). **Do the skill's research for "
            "real**: search the recognized frameworks it names (MLCommons "
            "AILuminate, NIST AI RMF, Microsoft Responsible AI, OWASP LLM Top "
            "10) and primary sources, read the results, and cite the actual "
            "pages you retrieve (title + URL + access date) exactly as the "
            "skill's citation rules require. Never fabricate a URL; if you "
            "cannot find a real source for an item, tag it `# source: repo "
            "spec` or `# source: uncited — needs review`. Still reuse the repo "
            "behavior/judge presets from the preset catalog above.\n"
        )
    else:
        research_bullet = (
            "- You run inside `assert-ai init` and do **not** have live "
            "web-browsing tools. Never claim to have retrieved pages this "
            "session and never fabricate citation URLs. Ground behavior "
            "categories and dimensions in your knowledge of the recognized "
            "frameworks the skill names (MLCommons AILuminate, NIST AI RMF, "
            "Microsoft Responsible AI, OWASP LLM Top 10) and, above all, reuse "
            "the repo behavior/judge presets from the preset catalog above. Tag "
            "each researched item with the framework it draws on (e.g. "
            "`# source: NIST AI RMF (model knowledge)`) instead of a URL, and "
            "skip the skill's `# References` URL list. The systematize stage's "
            "`web_search: true` performs the live enrichment when the pipeline "
            "runs.\n"
        )
    return (
        "## Harm Eval Template Skill (for the Automatic harm-template flow)\n\n"
        "When the user chooses the Automatic harm-template flow, follow the "
        "methodology below to design the config. Adapt it to this "
        "conversation's runtime:\n\n"
        f"{research_bullet}"
        "- Produce the config through the init `ask`/`propose`/`done` protocol as "
        "a single `yaml` string in your JSON response. Do **not** write files, "
        "reference an output path, or emit YAML frontmatter — `assert-ai init` "
        "owns file writing.\n"
        "- Keep everything customer-safe: describe the harm for detection and "
        "refusal only, never operational harmful content.\n\n"
        "---\n\n"
        f"{skill_text}\n"
    )


def _build_web_capability_section(web_search: bool) -> str:
    """State whether the design agent has live web research this session.

    Emitted for both flows (not just the harm template) so the guided
    conversation also knows it can ground answers in current sources.
    Returns an empty string when web search is off, matching the
    knowledge-only default.
    """
    if not web_search:
        return ""
    return (
        "## Live Web Research\n\n"
        "You have a live `web_search` tool this session (OpenAI/Azure "
        "`web_search_preview` via the Responses API). Use it to ground behavior "
        "specs, taxonomies, and judge rubrics in current, authoritative "
        "sources — search before relying on memory for factual or fast-moving "
        "topics, prefer recognized frameworks and primary sources, and when you "
        "cite something, cite the real page you actually retrieved (title + "
        "URL). Never invent a URL.\n"
    )


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
    dumped = dump_yaml(preset)
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
    dumped = dump_yaml(preset)
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


def _build_default_model_hint(default_model_hint: str | None) -> str:
    """Include the pipeline default_model pre-seed from --default-model."""
    if not default_model_hint:
        return ""
    return (
        "## Pipeline default_model Hint (from --default-model)\n\n"
        f"The user pre-seeded the pipeline `default_model` as: `{default_model_hint}`. "
        "Confirm this value with the user during the Pipeline Default Model step "
        "rather than asking from scratch.\n"
    )


# ── Public API ─────────────────────────────────────────────────

def build_system_message(
    *,
    seed_path: Path | None = None,
    behavior: str | None = None,
    judge_preset: str | None = None,
    dimensions: list[str] | None = None,
    describe: str | None = None,
    model: str = "azure/gpt-5.4-mini",
    default_model_hint: str | None = None,
    web_search: bool = False,
) -> str:
    """Assemble the full system message for the design agent.

    Loads the prompt template from ``prompts/init_system.md`` and appends
    contextual sections based on CLI flags.  Applies token-budget
    trimming when the assembled prompt is too large for the model.
    """
    template = load_prompt_text(_PROMPT_FILENAME)

    # Load the harm-template skill once and reuse it in both the full and
    # trimmed section lists — it drives the Automatic harm-template flow and
    # is small relative to the schema reference, so it survives trimming. Its
    # research bullet, plus the standalone capability note, reflect whether
    # live web search is available this session.
    harm_skill = _build_harm_skill_section(web_search)
    web_capability = _build_web_capability_section(web_search)

    # Build optional sections.
    sections = [
        _build_schema_reference(),
        _build_preset_catalog(),
        web_capability,
        harm_skill,
        _build_seed_section(seed_path),
        _build_behavior_section(behavior),
        _build_judge_section(judge_preset),
        _build_dimension_hints(dimensions),
        _build_description_section(describe),
        _build_default_model_hint(default_model_hint),
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
            web_capability,
            harm_skill,
            _build_seed_section(seed_path),
            _build_behavior_section(behavior),
            _build_judge_section(judge_preset),
            _build_dimension_hints(dimensions),
            _build_description_section(describe),
            _build_default_model_hint(default_model_hint),
        ]
        full_prompt = template + "\n\n" + "\n\n".join(s for s in trimmed_sections if s)
    elif estimated > ctx_window * _WARN_THRESHOLD:
        log.warning(
            "System prompt (~%d tokens) exceeds 50%% of %s context window "
            "(%d tokens). Consider using a model with a larger context window.",
            estimated, model, ctx_window,
        )

    return full_prompt
