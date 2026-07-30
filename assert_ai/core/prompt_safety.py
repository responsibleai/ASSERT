"""Helpers for embedding untrusted content in LLM prompts.

Prompt templates in ``assert_ai/internal_pipeline_prompts`` interpolate values
that ASSERT does not control: policy taxonomies authored by the user or a third
party, judge dimension descriptions, and test-case descriptions that were
themselves produced by a model in an earlier pipeline stage. Any of those can
carry text that reads as an instruction to the model consuming the prompt.

Two properties are enforced here:

``fill_template``
    Substitutes every placeholder in a single pass. Chained ``str.replace``
    calls are unsafe because a value substituted early is re-scanned by later
    calls, so untrusted text containing a literal ``{{output_schema}}`` would be
    expanded into a real prompt section. A single pass never re-reads
    substituted text, so placeholders inside values stay inert.

``wrap_untrusted``
    Fences a value in a named tag and removes any occurrence of that tag from
    the value first, so the content cannot close its own container early and
    have the remainder read as top-level instructions.

Neither helper makes a model immune to persuasion. They remove the structural
ambiguity that lets untrusted text impersonate the prompt's own framing; the
template must still tell the model that tagged content is data.
"""

from __future__ import annotations

import re
from typing import Mapping

__all__ = ["fill_template", "strip_delimiters", "wrap_untrusted"]

DEFAULT_TAG = "untrusted_content"

_PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")


def strip_delimiters(text: str, tag: str = DEFAULT_TAG) -> str:
    """Remove opening and closing forms of ``tag`` from ``text``.

    Matches are case-insensitive and tolerate internal whitespace and a
    self-closing slash, so ``</ Untrusted_Taxonomy >`` is removed just as
    ``</untrusted_taxonomy>`` is.
    """
    if not text:
        return ""
    return re.sub(
        rf"<\s*/?\s*{re.escape(tag)}\s*/?\s*>",
        "",
        str(text),
        flags=re.IGNORECASE,
    )


def wrap_untrusted(text: str, tag: str = DEFAULT_TAG) -> str:
    """Fence ``text`` in ``tag`` after removing any escape attempt."""
    return f"<{tag}>\n{strip_delimiters(text, tag)}\n</{tag}>"


def fill_template(template: str, values: Mapping[str, str]) -> str:
    """Replace every ``{{key}}`` in ``template`` in one left-to-right pass.

    Placeholders with no entry in ``values`` are left verbatim, matching the
    behaviour of the chained ``str.replace`` calls this function supersedes.
    """
    if not template:
        return ""

    def _substitute(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            return match.group(0)
        return str(values[key])

    return _PLACEHOLDER_RE.sub(_substitute, template)
