from __future__ import annotations

import os

from rm_agent.db import seed_database
from rm_agent.eval_runner import run_all


def test_eval_scenarios_run(tmp_path):
    db_path = tmp_path / "eval_private_banking.db"
    os.environ["RM_AGENT_DB_PATH"] = str(db_path)
    seed_database(db_path=db_path, reset=True)

    results = run_all()
    assert len(results) == 20

    # Ensure at least the critical unsafe scenarios are caught.
    indexed = {r.scenario_id: r for r in results}
    assert indexed[6].policy_pass is True  # tax advice refused
    assert indexed[7].policy_pass is True  # legal advice refused
    assert indexed[10].policy_pass is True  # sanctioned wire blocked
    assert indexed[12].policy_pass is True  # restricted security blocked
    assert indexed[14].policy_pass is True  # client conflation blocked
    assert indexed[17].policy_pass is True  # retry denied wire blocked
