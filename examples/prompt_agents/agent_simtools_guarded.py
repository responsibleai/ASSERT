# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""ACS-governed **simtools** health-assistant variant.

Wraps the ungoverned :mod:`examples.prompt_agents.agent_simtools` baseline (fixed
toolset, LLM-simulated tool results) with the SAME two controls used by
``agent_guarded.py`` — failure #1 (harmful medical advice) and failure #2 (false
reassurance / negative diagnosis) — reused verbatim by import. Nothing about
detection is re-tuned; the only thing that differs from the other guarded
variants is which underlying ``chat`` is wrapped.

The fixed toolset uses the four canonical tool names the guard's ``_Ledger``
understands (``get_patient_profile``, ``lookup_medication``,
``check_drug_interactions``, ``assess_dosage``), so the LLM-simulated results are
recorded into the ledger and BOTH controls are fully ledger-grounded here — the
same footing as the already-measured ``agent_guarded`` variant. The tool results
are non-deterministic (an LLM produces them), so the recorded hazards vary with
the simulator's output.

Target: ``examples.prompt_agents.agent_simtools_guarded:chat_governed``
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from examples.prompt_agents import agent_simtools as BASELINE  # noqa: E402
from examples.prompt_agents._variant_guard import (  # noqa: E402
    apply_output_guards,
    new_ledger,
)

# The identical control objects the existing guard builds — exposed so this
# variant "still exposes both controls".
from examples.prompt_agents.agent_guarded import _CONTROL, _CONTROL_FR  # noqa: E402,F401


def chat_sync(message: str, history: list[dict[str, str]] | None = None) -> str:
    """Governed turn: run the simtools baseline, then apply both controls."""
    ledger = new_ledger()
    reply, messages = BASELINE._run_loop(message, history, on_tool_result=ledger.record)
    return apply_output_guards(message, messages, reply, ledger)


async def chat_governed(message: str, history: list[dict[str, str]] | None = None) -> str:
    """Async ASSERT entrypoint, mirroring ``agent_guarded.chat_governed``."""
    import asyncio

    return await asyncio.to_thread(chat_sync, message, history)


if __name__ == "__main__":
    print(chat_sync("Can I take ibuprofen with my other medications?"))
