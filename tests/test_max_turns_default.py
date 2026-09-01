# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Tests that the tester turn budget moves for new configs, not existing ones.

The harm-eval methodology prefers a 6-turn budget, but the value a config
resolves to when it omits ``max_turns`` is the meaning of every config already
in use. Lowering the fallback would silently re-scope those runs, so the
preferred value is carried by what the generators *write* rather than by what
the loader *assumes*.
"""

from __future__ import annotations

import copy
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml

from assert_ai.config import load_runtime_context
from assert_ai.core.config_model import (
    DEFAULT_TESTER_MAX_TURNS,
    GENERATED_TESTER_MAX_TURNS,
)
from assert_ai.init._design_agent import _normalize_yaml
from assert_ai.init._emit import apply_generated_defaults, emit_config
from assert_ai.stages import STAGES

_REPO_ROOT = Path(__file__).resolve().parents[1]
_TEMPLATE = (
    _REPO_ROOT
    / ".claude"
    / "skills"
    / "run-assert-eval"
    / "assets"
    / "eval-config-template.yaml"
)
_INIT_SYSTEM = (
    _REPO_ROOT / "assert_ai" / "internal_pipeline_prompts" / "init_system.md"
)

# The value configs written before the harm-eval templates resolve to. Changing
# this is a migration, not an edit.
_LEGACY_OMITTED_MAX_TURNS = 10

# The value the generators write explicitly into new configs.
_PREFERRED_NEW_MAX_TURNS = 6

_EXPLICIT_MAX_TURNS = re.compile(r"^\s*max_turns:\s*(\d+)", re.MULTILINE)


def _config_without_max_turns():
    return {
        "suite": "test-suite",
        "behavior": {"name": "test_behavior"},
        "pipeline": {
            "test_set": {"prompt": {"model": {"name": "azure/gpt-5.4"}}},
            "inference": {"target": {"model": {"name": "azure/gpt-5.4"}}},
            "judge": {"model": {"name": "azure/gpt-5.4"}},
        },
    }


class OmittedMaxTurnsTest(unittest.TestCase):
    def test_omitted_max_turns_keeps_its_legacy_value(self) -> None:
        """An existing config must not change meaning because a default moved."""
        ctx = load_runtime_context(
            _config_without_max_turns(), Path("test.yaml"), stage_modules=STAGES
        )
        self.assertEqual(
            ctx["evaluation"].inference.max_turns,
            _LEGACY_OMITTED_MAX_TURNS,
            "configs that omit max_turns must keep the turn budget they already "
            "ran with; lowering it silently re-scopes existing evaluations",
        )

    def test_constant_matches_the_resolved_fallback(self) -> None:
        """Guards against the constant and the loader drifting apart."""
        self.assertEqual(DEFAULT_TESTER_MAX_TURNS, _LEGACY_OMITTED_MAX_TURNS)
        self.assertEqual(GENERATED_TESTER_MAX_TURNS, _PREFERRED_NEW_MAX_TURNS)
        self.assertNotEqual(
            DEFAULT_TESTER_MAX_TURNS,
            GENERATED_TESTER_MAX_TURNS,
            "if these collapse to one value the migration has been undone",
        )

    def test_an_explicit_value_still_wins(self) -> None:
        cfg = _config_without_max_turns()
        cfg["pipeline"]["inference"]["max_turns"] = _PREFERRED_NEW_MAX_TURNS
        ctx = load_runtime_context(cfg, Path("test.yaml"), stage_modules=STAGES)
        self.assertEqual(
            ctx["evaluation"].inference.max_turns, _PREFERRED_NEW_MAX_TURNS
        )


class GeneratedConfigsCarryTheValueTest(unittest.TestCase):
    """The preferred value has to be written, or new configs inherit the legacy one."""

    def _assert_writes_six(self, path: Path) -> None:
        self.assertTrue(path.exists(), f"{path} is missing")
        found = _EXPLICIT_MAX_TURNS.findall(path.read_text(encoding="utf-8"))
        self.assertTrue(
            found,
            f"{path.name} must write max_turns explicitly; omitting it makes new "
            f"configs resolve to the legacy {_LEGACY_OMITTED_MAX_TURNS}",
        )
        for value in found:
            self.assertEqual(
                int(value),
                _PREFERRED_NEW_MAX_TURNS,
                f"{path.name} should generate max_turns "
                f"{_PREFERRED_NEW_MAX_TURNS}, got {value}",
            )

    def test_harm_eval_template_writes_max_turns_explicitly(self) -> None:
        self._assert_writes_six(_TEMPLATE)

    def test_init_system_prompt_writes_max_turns_explicitly(self) -> None:
        self._assert_writes_six(_INIT_SYSTEM)


class EmittedConfigStatesTheValueTest(unittest.TestCase):
    """`assert-ai init` must not depend on the model copying the skeleton.

    The prompt tells the design agent to write ``max_turns``, but an
    instruction to a model is not a guarantee. If it omits the key the emitted
    config silently resolves to the legacy fallback, which is the exact
    substitution the split of the two constants exists to prevent.
    """

    def _emit(self, cfg: dict) -> dict:
        with TemporaryDirectory() as tmp_dir:
            out = Path(tmp_dir) / "eval.yaml"
            emit_config(yaml.safe_dump(cfg), out)
            return yaml.safe_load(out.read_text(encoding="utf-8"))

    def test_emitted_config_states_max_turns_when_the_model_omits_it(self) -> None:
        written = self._emit(_config_without_max_turns())
        self.assertEqual(
            written["pipeline"]["inference"].get("max_turns"),
            _PREFERRED_NEW_MAX_TURNS,
            "a generated config must state its turn budget rather than inherit "
            f"the legacy {_LEGACY_OMITTED_MAX_TURNS}",
        )

    def test_emitted_config_resolves_to_the_generated_value(self) -> None:
        """The end the user actually feels: what the run uses."""
        written = self._emit(_config_without_max_turns())
        ctx = load_runtime_context(written, Path("eval.yaml"), stage_modules=STAGES)
        self.assertEqual(
            ctx["evaluation"].inference.max_turns, _PREFERRED_NEW_MAX_TURNS
        )

    def test_an_explicit_choice_is_never_overwritten(self) -> None:
        cfg = _config_without_max_turns()
        cfg["pipeline"]["inference"]["max_turns"] = 12
        self.assertEqual(self._emit(cfg)["pipeline"]["inference"]["max_turns"], 12)

    def test_applying_twice_changes_nothing(self) -> None:
        """The design agent normalizes before emit, so this runs more than once."""
        once = apply_generated_defaults(_config_without_max_turns())
        twice = apply_generated_defaults(copy.deepcopy(once))
        self.assertEqual(once, twice)

    def test_a_config_without_an_inference_stage_is_left_alone(self) -> None:
        cfg = _config_without_max_turns()
        del cfg["pipeline"]["inference"]
        self.assertNotIn("inference", self._emit(cfg)["pipeline"])

    def test_the_design_agent_normalizer_states_the_value(self) -> None:
        """Covers the config shown to the user and any saved draft, not just the file."""
        normalized = yaml.safe_load(
            _normalize_yaml(yaml.safe_dump(_config_without_max_turns()))
        )
        self.assertEqual(
            normalized["pipeline"]["inference"].get("max_turns"),
            _PREFERRED_NEW_MAX_TURNS,
        )


if __name__ == "__main__":
    unittest.main()
