"""Interactive design-agent loop for ``p2m init``.

Drives a multi-turn conversation with an LLM that follows a three-action
protocol (ask / propose / done) to collaboratively design an eval config.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import yaml
from rich.console import Console
from rich.syntax import Syntax

from p2m.init._context import build_system_message
from p2m.init._llm import chat_completion
from p2m.init._validate import validate_proposed_yaml
from p2m.core.model_client import (
    LLMAuthError,
    LLMInputError,
    LLMProviderError,
    LLMRateLimitError,
)

log = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────

_VALID_ACTIONS = {"ask", "propose", "done"}
_CORRECTION_MSG = (
    "Your last response was not valid JSON. "
    "Respond with a JSON object containing 'action', 'content', "
    "and optionally 'yaml'."
)
_VALIDATION_FIX_MSG = (
    "The proposed YAML failed validation with the following errors:\n{errors}\n"
    "Please fix these issues and re-propose."
)


# ── Data types ─────────────────────────────────────────────────

@dataclass
class ParsedAction:
    action: str
    content: str
    yaml_str: str | None = None


@dataclass
class ParseError:
    raw: str
    reason: str


# ── JSON Parsing ───────────────────────────────────────────────

_FENCE_RE = re.compile(
    r"^\s*```(?:json)?\s*\n(.*?)\n\s*```\s*$",
    re.DOTALL,
)


def _strip_fences(text: str) -> str:
    """Remove markdown code fences wrapping JSON."""
    m = _FENCE_RE.search(text)
    if m:
        inner = m.group(1)
        # Handle double fencing (LLMs sometimes emit ```json\n```json\n...)
        m2 = _FENCE_RE.search(inner)
        if m2:
            return m2.group(1).strip()
        return inner.strip()
    return text.strip()


def _parse_action(raw_text: str) -> ParsedAction | ParseError:
    """Parse an LLM response into a structured action."""
    cleaned = _strip_fences(raw_text)
    try:
        data = json.loads(cleaned)
    except (json.JSONDecodeError, TypeError) as exc:
        return ParseError(raw=raw_text, reason=f"JSON parse error: {exc}")

    if not isinstance(data, dict):
        return ParseError(raw=raw_text, reason="Expected a JSON object")

    action = data.get("action")
    if action not in _VALID_ACTIONS:
        return ParseError(
            raw=raw_text,
            reason=f"Invalid action: {action!r}. Must be one of {sorted(_VALID_ACTIONS)}",
        )

    content = data.get("content", "")
    if not isinstance(content, str) or not content.strip():
        return ParseError(raw=raw_text, reason="'content' must be a non-empty string")

    yaml_str = data.get("yaml")
    if action in ("propose", "done") and not yaml_str:
        return ParseError(
            raw=raw_text,
            reason=f"Action '{action}' requires a 'yaml' field",
        )

    return ParsedAction(action=action, content=content.strip(), yaml_str=yaml_str)


# ── User interaction helpers ───────────────────────────────────

def _prompt_user(question: str, console: Console) -> str:
    """Display a question and collect the user's answer."""
    console.print(f"\n[bold]? {question}[/bold]\n")
    try:
        answer = input("> ")
    except EOFError:
        answer = ""
    return answer.strip()


def _prompt_accept(yaml_str: str, console: Console, no_color: bool) -> str:
    """Show proposed YAML and ask accept/refine/skip."""
    console.print("\n--- proposed eval.yaml ---")
    display = Console(
        highlight=False,
        color_system=None if no_color else "auto",
        stderr=True,
    )
    display.print(Syntax(yaml_str, "yaml", theme="monokai"))
    console.print("---\n")
    try:
        choice = input("[a]ccept / [r]efine / [s]kip > ").strip().lower()
    except EOFError:
        choice = "s"
    return choice


# ── Main loop ──────────────────────────────────────────────────

def run_design_loop(
    *,
    model: str,
    describe: str | None,
    seed_yaml: str | None,
    behavior_preset: str | None,
    judge_preset: str | None,
    dimension_hints: str | None,
    non_interactive: bool,
    max_turns: int,
    console: Console,
    no_color: bool,
) -> str | None:
    """Run the design agent conversation loop.

    Returns the final YAML string, or ``None`` if the user aborted
    or the loop exhausted its turn budget.
    """
    dim_list = [d.strip() for d in dimension_hints.split(",")] if dimension_hints else None

    system_msg = build_system_message(
        behavior=behavior_preset,
        judge_preset=judge_preset,
        dimensions=dim_list,
        describe=describe,
        model=model,
    )

    messages: list[dict[str, str]] = [{"role": "system", "content": system_msg}]

    # Build initial user message from CLI flags.
    initial_parts: list[str] = []
    if describe:
        initial_parts.append(f"System description: {describe}")
    if seed_yaml:
        initial_parts.append(f"Existing config to extend:\n```yaml\n{seed_yaml}\n```")
    if behavior_preset:
        initial_parts.append(f"Use behavior preset: {behavior_preset}")
    if judge_preset:
        initial_parts.append(f"Use judge preset: {judge_preset}")
    if dimension_hints:
        initial_parts.append(f"Dimension hints: {dimension_hints}")
    if non_interactive:
        initial_parts.append(
            "Generate the config immediately without asking questions. "
            "Respond with action 'done'."
        )

    if initial_parts:
        messages.append({"role": "user", "content": "\n".join(initial_parts)})
    else:
        messages.append({
            "role": "user",
            "content": "I want to create an eval config. Please ask me about my system.",
        })

    best_draft: str | None = None
    best_errors: list[str] = []

    try:
        for turn in range(max_turns):
            log.debug("Design loop turn %d/%d", turn + 1, max_turns)

            try:
                raw = chat_completion(
                    model=model,
                    messages=messages,
                    response_format={"type": "json_object"},
                )
            except (LLMAuthError, LLMInputError, LLMRateLimitError, LLMProviderError) as exc:
                console.print(f"[init] error: {exc}")
                return best_draft

            result = _parse_action(raw)

            if isinstance(result, ParseError):
                log.debug("Parse error: %s", result.reason)
                messages.append({"role": "assistant", "content": raw})
                messages.append({"role": "user", "content": _CORRECTION_MSG})
                continue

            messages.append({"role": "assistant", "content": raw})

            # ── ask ────────────────────────────────────────────
            if result.action == "ask":
                if non_interactive:
                    messages.append({
                        "role": "user",
                        "content": (
                            "Please generate the config now with the information "
                            "provided. Use action 'done'."
                        ),
                    })
                    continue
                answer = _prompt_user(result.content, console)
                if not answer:
                    answer = "(no answer)"
                messages.append({"role": "user", "content": answer})
                continue

            # ── propose ────────────────────────────────────────
            if result.action == "propose":
                assert result.yaml_str is not None
                ok, errors = validate_proposed_yaml(result.yaml_str)
                if not ok:
                    best_draft = result.yaml_str
                    best_errors = errors
                    error_text = "\n".join(f"- {e}" for e in errors)
                    messages.append({
                        "role": "user",
                        "content": _VALIDATION_FIX_MSG.format(errors=error_text),
                    })
                    continue

                best_draft = result.yaml_str
                best_errors = []

                if non_interactive:
                    return _normalize_yaml(result.yaml_str)

                choice = _prompt_accept(result.yaml_str, console, no_color)
                if choice.startswith("a"):
                    return _normalize_yaml(result.yaml_str)
                elif choice.startswith("r"):
                    try:
                        refinement = input("What would you like to change? > ").strip()
                    except EOFError:
                        refinement = ""
                    messages.append({
                        "role": "user",
                        "content": refinement or "Please try again.",
                    })
                    continue
                else:
                    console.print("[init] Skipped.")
                    return None

            # ── done ───────────────────────────────────────────
            if result.action == "done":
                assert result.yaml_str is not None
                ok, errors = validate_proposed_yaml(result.yaml_str)
                if ok:
                    return _normalize_yaml(result.yaml_str)
                best_draft = result.yaml_str
                best_errors = errors
                error_text = "\n".join(f"- {e}" for e in errors)
                messages.append({
                    "role": "user",
                    "content": _VALIDATION_FIX_MSG.format(errors=error_text),
                })
                continue

        # Exhausted turn budget.
        console.print(f"[init] Reached maximum turns ({max_turns}).")
        if best_draft:
            _save_draft(best_draft, best_errors, console)
        return best_draft if best_draft and not best_errors else None

    except KeyboardInterrupt:
        console.print("")
        if best_draft:
            _save_draft(best_draft, best_errors, console)
            console.print("[init] Interrupted. Draft saved.")
        else:
            console.print("[init] Interrupted. No draft to save.")
        raise SystemExit(130)


# ── Helpers ────────────────────────────────────────────────────

def _normalize_yaml(yaml_str: str) -> str:
    """Roundtrip through PyYAML for consistent formatting."""
    data = yaml.safe_load(yaml_str)
    return yaml.dump(data, default_flow_style=False, sort_keys=False)


def _save_draft(yaml_str: str, errors: list[str], console: Console) -> None:
    """Write a draft file alongside any errors."""
    draft_path = Path("eval.draft.yaml")
    try:
        normalized = _normalize_yaml(yaml_str)
        draft_path.write_text(normalized, encoding="utf-8")
        console.print(f"[init] Draft saved to {draft_path}")
        if errors:
            console.print("[init] Remaining validation errors:")
            for e in errors:
                console.print(f"  - {e}")
    except Exception as exc:
        log.warning("Failed to save draft: %s", exc)
