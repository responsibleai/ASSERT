"""Interactive design-agent loop for ``assert-ai init``.

Drives a multi-turn conversation with an LLM that follows a three-action
protocol (ask / propose / done) to collaboratively design an eval config.
"""

from __future__ import annotations

import json
import logging
import os
import re
import select
import sys
from dataclasses import dataclass
from pathlib import Path

try:
    import readline  # noqa: F401 — enables arrow-key line editing for input()
except ImportError:
    pass

import yaml
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax

from assert_ai.init._context import build_system_message, estimate_tokens, context_window_for
from assert_ai.init._llm import chat_completion
from assert_ai.init._validate import validate_proposed_yaml
from assert_ai.core.model_client import (
    LLMAuthError,
    LLMInputError,
    LLMProviderError,
    LLMRateLimitError,
)

log = logging.getLogger(__name__)


def _flush_stdin() -> None:
    """Discard any buffered stdin so stale keypresses don't leak into input()."""
    try:
        import termios  # Unix only

        termios.tcflush(sys.stdin, termios.TCIFLUSH)
    except Exception:  # noqa: BLE001 — Windows / non-TTY / already closed
        pass


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
    except json.JSONDecodeError:
        # LLMs sometimes emit duplicate JSON lines (JSONL-style).
        # Try parsing just the first non-empty line.
        first_line = next((l for l in cleaned.splitlines() if l.strip()), "")
        try:
            data = json.loads(first_line)
        except (json.JSONDecodeError, TypeError) as exc:
            return ParseError(raw=raw_text, reason=f"JSON parse error: {exc}")
    except TypeError as exc:
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
    """Display a question and collect the user's answer.

    For normal typing, a single Enter submits the answer.  If the user
    pastes multi-line text, additional lines are captured automatically
    via stdin buffer detection.
    """
    console.print()
    console.print(Panel(
        Markdown(question),
        title="[bold]Question[/bold]",
        title_align="left",
        border_style="blue",
        padding=(0, 1),
    ))
    _flush_stdin()
    try:
        first = input("› ")
    except EOFError:
        return ""
    if not first:
        return ""

    lines = [first]
    # Drain any remaining pasted lines sitting in the stdin buffer.
    try:
        fd = sys.stdin.fileno()
        while select.select([fd], [], [], 0.05)[0]:
            chunk = os.read(fd, 4096)
            if not chunk:
                break
            lines.extend(chunk.decode("utf-8", errors="replace").splitlines())
    except (OSError, ValueError):
        pass  # select unavailable on this platform — single-line fallback

    # Echo additional pasted lines so the user can see everything they sent.
    if len(lines) > 1:
        for extra in lines[1:]:
            console.print(f"  {extra}")

    return "\n".join(lines).strip()


def _prompt_accept(yaml_str: str, console: Console, no_color: bool) -> str:
    """Show proposed YAML and ask accept/refine/skip."""
    console.print()
    theme = "monokai" if not no_color else "default"
    console.print(Panel(
        Syntax(yaml_str, "yaml", theme=theme),
        title="[bold green]Proposed eval.yaml[/bold green]",
        border_style="green",
        padding=(0, 1),
    ))
    _flush_stdin()
    try:
        choice = input("[a]ccept / [r]efine / [s]kip › ").strip().lower()
    except EOFError:
        choice = "s"
    return choice


# ── Main loop ──────────────────────────────────────────────────

def run_design_loop(
    *,
    model: str,
    describe: str | None,
    seed_yaml: str | None,
    seed_path: Path | None = None,
    behavior_preset: str | None,
    judge_preset: str | None,
    dimension_hints: str | None,
    default_model_hint: str | None = None,
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
        seed_path=seed_path,
        behavior=behavior_preset,
        judge_preset=judge_preset,
        dimensions=dim_list,
        describe=describe,
        model=model,
        default_model_hint=default_model_hint,
    )

    messages: list[dict[str, str]] = [{"role": "system", "content": system_msg}]

    # Build initial user message from CLI flags.
    initial_parts: list[str] = []
    if describe:
        initial_parts.append(f"System description: {describe}")
    if seed_yaml:
        # The seed is *also* included in the system prompt (via seed_path)
        # where it participates in token-budget trimming.  We duplicate it
        # here so the LLM treats it as user-submitted input it can question,
        # extend, or restructure — rather than as immutable instructions.
        seed_tokens = estimate_tokens(seed_yaml)
        ctx_window = context_window_for(model)
        if seed_tokens > ctx_window * 0.10:
            log.warning(
                "Seed config is large (~%d tokens, %.0f%% of %s context "
                "window). Consider trimming it to avoid prompt-too-long errors.",
                seed_tokens, seed_tokens / ctx_window * 100, model,
            )
        initial_parts.append(f"Existing config to extend:\n```yaml\n{seed_yaml}\n```")
    if behavior_preset:
        initial_parts.append(f"Use behavior preset: {behavior_preset}")
    if judge_preset:
        initial_parts.append(f"Use judge preset: {judge_preset}")
    if dimension_hints:
        initial_parts.append(f"Dimension hints: {dimension_hints}")
    if default_model_hint:
        initial_parts.append(
            f"Pipeline default_model hint (from --default-model): {default_model_hint}. "
            "Confirm this with the user rather than re-asking from scratch."
        )
    initial_parts.append(
        f"Design-agent model (drives this conversation, NOT the target, NOT the pipeline default_model): {model}. "
        "When asking about pipeline default_model, you may suggest this as a reasonable starting default if the user has no preference."
    )
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
            log.info("Turn %d/%d", turn + 1, max_turns)

            try:
                with console.status("Thinking\u2026", spinner="dots"):
                    raw = chat_completion(
                        model=model,
                        messages=messages,
                        response_format={"type": "json_object"},
                    )
            except (LLMAuthError, LLMInputError, LLMRateLimitError, LLMProviderError) as exc:
                log.error("LLM error: %s", exc)
                if best_draft and best_errors:
                    _save_draft(best_draft, best_errors, console)
                    return None
                return best_draft

            result = _parse_action(raw)

            if isinstance(result, ParseError):
                log.info("  \u21b3 malformed response \u2014 retrying")
                log.debug("  Parse error detail: %s", result.reason)
                log.debug("  Raw response: %.300s", raw)
                messages.append({"role": "assistant", "content": raw})
                messages.append({"role": "user", "content": _CORRECTION_MSG})
                continue

            messages.append({"role": "assistant", "content": raw})

            # ── ask ────────────────────────────────────────────
            if result.action == "ask":
                if non_interactive:
                    log.info("  \u21b3 skipping question (non-interactive mode)")
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
                    log.info("  \u21b3 proposed config has %d validation error(s) \u2014 asking LLM to fix", len(errors))
                    for err in errors:
                        log.debug("    - %s", err)
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
                    _flush_stdin()
                    try:
                        refinement = input("What would you like to change? › ").strip()
                    except EOFError:
                        refinement = ""
                    messages.append({
                        "role": "user",
                        "content": refinement or "Please try again.",
                    })
                    continue
                else:
                    log.info("Skipped.")
                    return None

            # ── done ───────────────────────────────────────────
            if result.action == "done":
                assert result.yaml_str is not None
                ok, errors = validate_proposed_yaml(result.yaml_str)
                if ok:
                    best_draft = result.yaml_str
                    best_errors = []

                    if non_interactive:
                        return _normalize_yaml(result.yaml_str)

                    choice = _prompt_accept(result.yaml_str, console, no_color)
                    if choice.startswith("a"):
                        return _normalize_yaml(result.yaml_str)
                    elif choice.startswith("r"):
                        _flush_stdin()
                        try:
                            refinement = input("What would you like to change? › ").strip()
                        except EOFError:
                            refinement = ""
                        messages.append({
                            "role": "user",
                            "content": refinement or "Please try again.",
                        })
                        continue
                    else:
                        log.info("Skipped.")
                        return None
                log.info("  \u21b3 final config has %d validation error(s) \u2014 asking LLM to fix", len(errors))
                for err in errors:
                    log.debug("    - %s", err)
                best_draft = result.yaml_str
                best_errors = errors
                error_text = "\n".join(f"- {e}" for e in errors)
                messages.append({
                    "role": "user",
                    "content": _VALIDATION_FIX_MSG.format(errors=error_text),
                })
                continue

        # Exhausted turn budget.
        log.warning("Reached maximum turns (%d).", max_turns)
        if best_draft:
            _save_draft(best_draft, best_errors, console)
        return best_draft if best_draft and not best_errors else None

    except KeyboardInterrupt:
        console.print("")
        if best_draft:
            _save_draft(best_draft, best_errors, console)
            log.info("Interrupted. Draft saved.")
        else:
            log.info("Interrupted. No draft to save.")
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
        log.info("Draft saved to %s", draft_path)
        if errors:
            log.warning("Remaining validation errors:")
            for e in errors:
                log.warning("  - %s", e)
    except Exception as exc:
        log.warning("Failed to save draft: %s", exc)
