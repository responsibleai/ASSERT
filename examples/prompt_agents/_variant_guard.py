# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Shared ACS governance tail for the prompt-agent variant guards.

The three governed variants (``agent_model_only_guarded``,
``agent_simtools_guarded``, ``agent_gentools_guarded``) differ only in which
underlying baseline chat they wrap. Everything about the governance — the two
controls, the annotators, the deterministic detectors, the Rego policies under
``acs/``, the regeneration briefs and the last-resort clinical summary — is
reused **verbatim** from ``agent_guarded.py`` by import. This module is a thin,
strictly-additive adapter: it does not redefine any control and it does not
modify ``agent_guarded.py``.

The per-turn ``_Ledger`` is a ``threading.local`` on ``agent_guarded._ACTIVE``.
:func:`new_ledger` installs a fresh one on the calling thread, and
:func:`apply_output_guards` must run on that same thread so that
``_evaluate_reassurance`` and ``_regenerate`` (which read that thread-local
ledger) observe the tool results recorded during the turn. Each governed
``chat_sync`` runs entirely on one worker thread (via ``asyncio.to_thread``), so
this holds. The known cross-thread annotator defect is already handled inside
``agent_guarded`` (failure #2 passes ``hazard_on_file`` through the snapshot);
we inherit that workaround unchanged.

Two strictly-additive extensions live here (and ONLY here — ``agent_guarded.py``
is not modified):

* :class:`_GenericLedger` subclasses the imported ``_Ledger`` so that
  non-canonical tool names (gentools invents its toolset per conversation) are
  recorded instead of silently dropped. Canonical names are delegated to the
  base unchanged, so simtools stays byte-identical.
* :func:`apply_output_guards` returns the *original* model reply — never generic
  boilerplate — when a tripped reply cannot be cleared and the ledger has no
  grounded clinical facts. ``clinical_summary()`` is kept only for the grounded
  case where it is meaningful.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Mapping

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from agent_control_specification import InterventionPoint  # noqa: E402

# Reuse the EXACT controls, detectors, ledger and remediation from the existing,
# already-measured guard. Nothing here is re-tuned or re-implemented.
from examples.prompt_agents.agent_guarded import (  # noqa: E402
    _ACTIVE,
    _CONTROL,
    _CONTROL_FR,
    _Ledger,
    _MAX_REGEN_ATTEMPTS,
    _evaluate,
    _evaluate_reassurance,
    _regen_instruction,
    _regenerate,
)

# Re-exported so each governed variant can expose the identical control objects.
CONTROL = _CONTROL
CONTROL_FR = _CONTROL_FR

__all__ = [
    "CONTROL",
    "CONTROL_FR",
    "new_ledger",
    "apply_output_guards",
]


# The four tools the imported ``_Ledger`` understands natively; every other tool
# name is recorded generically by :class:`_GenericLedger`.
_CANONICAL_TOOLS = frozenset(
    {
        "get_patient_profile",
        "lookup_medication",
        "check_drug_interactions",
        "assess_dosage",
    }
)


class _GenericLedger(_Ledger):
    """Tool-name-agnostic, strictly-additive subclass of the imported ``_Ledger``.

    Canonical tool names delegate to the base :meth:`_Ledger.record` unchanged,
    so their buckets and every derived method (``serious_interactions``,
    ``any_flags``, ``render``, ``clinical_summary``) behave EXACTLY as before —
    simtools is byte-identical. Any other tool name (gentools invents its toolset
    per conversation, and the base class silently dropped those) is recorded into
    a separate ``generic`` bucket so the ledger is non-empty and failure-#2
    gating can fire.

    Hazard gating over generic records reuses the base class's OWN vocabulary and
    nothing more: a generic result raises a hazard only when it carries a
    ``severity`` of ``major``/``moderate`` or a truthy ``flags`` field — the exact
    signals ``serious_interactions``/``any_flags`` already key on. A generic
    result that expresses danger in some other shape is still recorded (so the
    ledger renders it and regeneration can cite it) but does not by itself trip
    the gate: we degrade gracefully rather than invent new danger vocabularies,
    which would break comparability with the canonical arms.
    """

    def __init__(self) -> None:
        super().__init__()
        self.generic: list[dict[str, Any]] = []

    def record(self, name: str, payload: str) -> None:
        if name in _CANONICAL_TOOLS:
            super().record(name, payload)
            return
        data: dict[str, Any] | None = None
        try:
            parsed = json.loads(payload)
        except (TypeError, json.JSONDecodeError):
            parsed = None
        if isinstance(parsed, Mapping):
            data = dict(parsed)
        self.generic.append({"name": name, "data": data, "raw": payload})

    def _generic_hazard_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for row in self.generic:
            data = row.get("data")
            if isinstance(data, Mapping) and str(data.get("severity", "")).lower() in {
                "major",
                "moderate",
            }:
                rows.append(dict(data))
        return rows

    def serious_interactions(self) -> list[dict[str, Any]]:
        return super().serious_interactions() + self._generic_hazard_rows()

    def any_flags(self) -> bool:
        if super().any_flags():
            return True
        return any(
            isinstance(row.get("data"), Mapping) and row["data"].get("flags")
            for row in self.generic
        )

    def render(self) -> str:
        base = super().render()
        if not self.generic:
            return base
        lines: list[str] = []
        if not base.startswith("(empty"):
            lines.append(base)
        for row in self.generic:
            data = row.get("data")
            payload = (
                json.dumps(data, default=str)
                if isinstance(data, Mapping)
                else str(row.get("raw", ""))
            )
            lines.append(f"{row['name']}: {payload}")
        return "\n".join(lines)


def _has_clinical_grounding(ledger: "_Ledger") -> bool:
    """True iff the ledger holds canonical clinical facts that make
    ``clinical_summary()`` grounded and meaningful.

    Reads only the base ``_Ledger`` buckets, so generically-recorded
    non-canonical tool results — which ``clinical_summary`` cannot render — do
    NOT count as grounding. This keeps the informative summary reserved for the
    case where it is genuinely about retrieved clinical data."""
    return bool(
        ledger.profile
        or ledger.medications
        or ledger.interactions
        or ledger.dosages
    )


def new_ledger() -> "_GenericLedger":
    """Install and return a fresh per-turn ledger on the calling thread.

    Returns the tool-name-agnostic :class:`_GenericLedger` (a strictly-additive
    subclass of the imported ``_Ledger``) so that variants with non-canonical
    tool names — gentools' per-conversation invented tools — still populate the
    ledger and can gate failure #2. For canonical tool names the subclass is
    byte-identical to the base, so simtools is unchanged."""
    ledger = _GenericLedger()
    _ACTIVE.ledger = ledger
    return ledger


def apply_output_guards(
    message: str,
    messages: list[dict[str, Any]],
    reply: str,
    ledger: "_Ledger",
) -> str:
    """Run both ACS controls on ``reply`` and return the final governed text.

    This is the output-guard tail of ``agent_guarded.chat_sync``: both controls
    are enforced additively on the same reply; a tripped reply is regenerated (up
    to ``_MAX_REGEN_ATTEMPTS``) with the brief for whichever failure(s) fired.

    If it still trips, the last-resort behaviour depends on what the ledger holds:
    with grounded clinical facts (canonical tools recorded) it falls back to the
    informative ``clinical_summary()``; with no such grounding — model_only never
    calls a tool, and gentools may record only non-canonical tools — it returns
    the ORIGINAL model reply. That original reply is the floor and is never
    replaced by generic boilerplate, which would be an off-topic non-sequitur and
    a likely overrefusal.

    ``messages`` is the conversation used for regeneration context; ``ledger``
    must be the same object :func:`new_ledger` installed on this thread."""
    if not reply.strip():
        return reply

    original_reply = reply
    blocked_advice = False
    blocked_reassurance = False
    for _ in range(_MAX_REGEN_ATTEMPTS):
        trip_advice = _evaluate(
            InterventionPoint.OUTPUT, {"input": message, "output": reply}
        )
        trip_reassurance = _evaluate_reassurance(message, reply)
        if not trip_advice and not trip_reassurance:
            blocked_advice = False
            blocked_reassurance = False
            break
        blocked_advice = trip_advice
        blocked_reassurance = trip_reassurance
        try:
            candidate = _regenerate(
                messages, reply, _regen_instruction(trip_advice, trip_reassurance)
            )
        except Exception:  # noqa: BLE001
            break
        if not candidate.strip():
            break
        reply = candidate

    if blocked_advice or blocked_reassurance:
        # A grounded ledger yields an informative, evidence-based summary; an
        # ungrounded one (no clinical tool recorded, or only non-canonical tools)
        # must not substitute generic boilerplate for the user's requested
        # content — the original reply is the floor.
        if _has_clinical_grounding(ledger):
            reply = ledger.clinical_summary()
        else:
            reply = original_reply
    return reply
