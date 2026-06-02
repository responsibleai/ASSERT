# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import importlib.util
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import yaml

from assert_ai.core.model_client import ModelResponse
from assert_ai.core.io import row_behavior


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "tester_pairwise_eval.py"


def _load_script_module():
    module_name = "tester_pairwise_eval_test_module"
    spec = importlib.util.spec_from_file_location(module_name, SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load script module from {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


tester_pairwise_eval = _load_script_module()


def _pairwise_verdict(*, winner: str = "A") -> dict:
    return {
        "winner": winner,
        "confidence": "high",
        "rationale": "Conversation A stayed more convincingly in character across the exchange.",
        "a_evidence": "Turns 1-3 show natural emotional texture. Turn 5 drifts into operational detail.",
        "b_evidence": "Turns 1-2 are plausible. Turns 4-6 feel like benchmark branch coverage.",
        "dimension_rationales": {
            "realism": "A sounded more like the seeded person and held together better across turns.",
        },
        "a_strengths": ["More believable voice", "Better persona continuity"],
        "a_weaknesses": ["Could vary phrasing more"],
        "b_strengths": ["Plausible pressure"],
        "b_weaknesses": ["Felt more scripted"],
        "dimension_winners": {
            "realism": winner,
        },
    }


def _effectiveness_verdict(*, winner: str = "A") -> dict:
    return {
        "winner": winner,
        "confidence": "medium",
        "rationale": "Conversation A used the turns more effectively to surface informative target behavior.",
        "a_evidence": "Turns 2-4 surfaced clear taxonomy-relevant evidence. Turn 6 was redundant.",
        "b_evidence": "Turns 1-3 were direct but narrow. Turn 5 added new information.",
        "dimension_rationales": {
            "effectiveness": "A surfaced clearer evidence and used better strategy.",
        },
        "a_strengths": ["Better pivots", "Clearer evidence"],
        "a_weaknesses": ["Could have narrowed faster"],
        "b_strengths": ["More concise"],
        "b_weaknesses": ["Less informative"],
        "dimension_winners": {
            "effectiveness": winner,
        },
    }


async def _eval_response(model, prompt, *, options):
    del model, prompt, options
    return ModelResponse(text="# Pairwise Eval\n\nInterim interpretation.\n", model="judge-model")


def _invert_label(value: str) -> str:
    if value == "A":
        return "B"
    if value == "B":
        return "A"
    return value


def _verdict_for_prompt(prompt: str, *, canonical_winner: str = "A") -> dict:
    verdict = _pairwise_verdict(winner=canonical_winner)
    prompt_is_swapped = prompt.index("user turn from B") < prompt.index("user turn from A")
    if not prompt_is_swapped:
        return verdict

    swapped = dict(verdict)
    swapped["winner"] = _invert_label(str(verdict["winner"]))
    swapped["dimension_winners"] = {
        key: _invert_label(str(value))
        for key, value in verdict["dimension_winners"].items()
    }
    swapped["dimension_rationales"] = dict(verdict["dimension_rationales"])
    return swapped


def _effectiveness_verdict_for_prompt(prompt: str, *, canonical_winner: str = "A") -> dict:
    verdict = _effectiveness_verdict(winner=canonical_winner)
    prompt_is_swapped = prompt.index("user turn from B") < prompt.index("user turn from A")
    if not prompt_is_swapped:
        return verdict

    swapped = dict(verdict)
    swapped["winner"] = _invert_label(str(verdict["winner"]))
    swapped["dimension_winners"] = {
        key: _invert_label(str(value))
        for key, value in verdict["dimension_winners"].items()
    }
    swapped["dimension_rationales"] = dict(verdict["dimension_rationales"])
    return swapped


def _verdict_for_schema_prompt(schema_name: str, prompt: str, *, realism_winner: str = "A", effectiveness_winner: str = "A") -> dict:
    if schema_name == "tester_pairwise_effectiveness_judgment":
        return _effectiveness_verdict_for_prompt(prompt, canonical_winner=effectiveness_winner)
    return _verdict_for_prompt(prompt, canonical_winner=realism_winner)


class TesterPairwiseEvalHelpersTest(unittest.TestCase):
    def _test_case_row(self, *, test_case_id: str, behavior: str = "behavior") -> dict:
        return {
            "type": "scenario",
            "test_case_id": test_case_id,
            "behavior": "behavior",
            "dimensions": {"behavior": behavior},
            "seed": {
                "title": f"Title for {test_case_id}",
                "description": f"Scenario description for {test_case_id}.",
                "system_prompt": f"System prompt for {test_case_id}.",
            },
        }

    def _inference_row(
        self,
        *,
        test_case_id: str,
        run_label: str,
        behavior: str = "behavior",
        stop_reason: str = "max_turns",
    ) -> dict:
        return {
            "type": "scenario",
            "test_case_id": test_case_id,
            "behavior": "behavior",
            "dimensions": {"behavior": behavior},
            "target": "target-model",
            "tester_model": f"tester-{run_label}",
            "stop_reason": stop_reason,
            "events": [
                {
                    "view": ["target", "combined"],
                    "actor": "tester",
                    "edit": {
                        "type": "add_message",
                        "message": {
                            "role": "user",
                            "content": f"user turn from {run_label}",
                        },
                    },
                    "raw": None,
                },
                {
                    "view": ["target", "combined"],
                    "actor": "target",
                    "edit": {
                        "type": "add_message",
                        "message": {
                            "role": "assistant",
                            "content": f"assistant reply to {run_label}",
                        },
                    },
                    "raw": None,
                },
            ],
        }

    def test_build_pairwise_prompt_includes_metadata_and_both_transcripts(self) -> None:
        prompt = tester_pairwise_eval.build_pairwise_prompt(
            test_case_row=self._test_case_row(test_case_id="test-case-1"),
            transcript_a_row=self._inference_row(test_case_id="test-case-1", run_label="A"),
            transcript_b_row=self._inference_row(test_case_id="test-case-1", run_label="B"),
        )

        self.assertIn("Seed ID: test-case-1", prompt)
        self.assertIn("Title for test-case-1", prompt)
        self.assertIn("Scenario description for test-case-1.", prompt)
        self.assertIn("System prompt for test-case-1.", prompt)
        self.assertNotIn("Run:", prompt)
        self.assertNotIn("Stop reason:", prompt)
        self.assertNotIn("Visible user turns:", prompt)
        self.assertIn("user turn from A", prompt)
        self.assertIn("assistant reply to A", prompt)
        self.assertIn("user turn from B", prompt)
        self.assertIn("assistant reply to B", prompt)

    def test_build_pairwise_metrics_aggregates_wins_ties_and_slices(self) -> None:
        metrics = tester_pairwise_eval.build_pairwise_metrics(
            [
                {
                    "dimensions": {"behavior": "node-a"},
                    "judge_status": "ok",
                    "winner": "A",
                    "confidence": "high",
                    "effectiveness_judge_status": "ok",
                    "effectiveness_winner": "B",
                    "effectiveness_confidence": "medium",
                    "dimension_winners": {
                        "realism": "A",
                    },
                    "effectiveness_dimension_winners": {
                        "effectiveness": "B",
                    },
                    "a_policy_violation": False,
                    "b_policy_violation": True,
                    "a_overrefusal": False,
                    "b_overrefusal": False,
                },
                {
                    "dimensions": {"behavior": "node-a"},
                    "judge_status": "ok",
                    "winner": "tie",
                    "confidence": "low",
                    "effectiveness_judge_status": "ok",
                    "effectiveness_winner": "tie",
                    "effectiveness_confidence": "low",
                    "dimension_winners": {
                        "realism": "tie",
                    },
                    "effectiveness_dimension_winners": {
                        "effectiveness": "tie",
                    },
                    "a_policy_violation": False,
                    "b_policy_violation": False,
                    "a_overrefusal": True,
                    "b_overrefusal": True,
                },
                {
                    "dimensions": {"behavior": "node-b"},
                    "judge_status": "order_inconsistent",
                    "winner": None,
                    "confidence": None,
                    "dimension_winners": {},
                    "effectiveness_judge_status": "order_inconsistent",
                    "effectiveness_winner": None,
                    "effectiveness_confidence": None,
                    "effectiveness_dimension_winners": {},
                },
                {
                    "dimensions": {"behavior": "node-b"},
                    "judge_status": "judge_failed",
                    "winner": None,
                    "confidence": None,
                    "dimension_winners": {},
                    "effectiveness_judge_status": "judge_failed",
                    "effectiveness_winner": None,
                    "effectiveness_confidence": None,
                    "effectiveness_dimension_winners": {},
                },
            ],
            run_a="run-a",
            run_b="run-b",
            suite_id="suite-a",
            judge_model="judge-model",
            missing_pairs={"run_a_only_count": 1, "run_b_only_count": 2},
        )

        self.assertEqual(metrics["total_matched_pairs"], 4)
        self.assertEqual(metrics["scored_pairs"], 2)
        self.assertEqual(metrics["order_inconsistent_pairs"], 1)
        self.assertEqual(metrics["confidence"], {"high": 1, "medium": 0, "low": 1})
        self.assertEqual(metrics["low_confidence_pairs"], 1)
        self.assertEqual(metrics["wins"], {"A": 1, "B": 0, "tie": 1})
        self.assertEqual(metrics["by_behavior"]["node-a"]["wins"], {"A": 1, "B": 0, "tie": 1})
        self.assertEqual(metrics["by_behavior"]["node-b"]["judge_failures"], 1)
        self.assertEqual(metrics["by_behavior"]["node-b"]["order_inconsistent"], 1)
        self.assertEqual(metrics["by_dimension"]["realism"]["wins"]["A"], 1)
        self.assertEqual(metrics["effectiveness"]["wins"], {"A": 0, "B": 1, "tie": 1})
        self.assertEqual(metrics["effectiveness"]["by_dimension"]["effectiveness"]["wins"]["B"], 1)
        self.assertEqual(metrics["effectiveness"]["outcomes"]["policy_violation"]["b_only"]["count"], 1)
        self.assertEqual(metrics["effectiveness"]["outcomes"]["policy_violation"]["b_only"]["wins"]["B"], 1)
        self.assertEqual(metrics["axis_comparison"]["stability"]["both_ok"], 2)
        self.assertEqual(metrics["axis_comparison"]["stability"]["neither_ok"], 2)
        self.assertEqual(metrics["axis_comparison"]["winner_alignment"]["different"], 1)
        self.assertEqual(metrics["axis_comparison"]["winner_alignment"]["realism_a_effectiveness_b"], 1)

    def test_render_pairwise_summary_includes_effectiveness_behavior_results(self) -> None:
        summary = tester_pairwise_eval.render_pairwise_summary(
            {
                "run_a": "run-a",
                "run_b": "run-b",
                "suite_id": "suite-a",
                "judge_model": "judge-model",
                "total_matched_pairs": 1,
                "scored_pairs": 1,
                "order_inconsistent_pairs": 0,
                "judge_failures": 0,
                "order_consistency_rate": 1.0,
                "low_confidence_pairs": 0,
                "confidence": {"high": 1, "medium": 0, "low": 0},
                "wins": {"A": 1, "B": 0, "tie": 0},
                "by_dimension": {},
                "by_behavior": {
                    "realism-node": {
                        "wins": {"A": 1, "B": 0, "tie": 0},
                        "order_inconsistent": 0,
                        "judge_failures": 0,
                    }
                },
                "common_error_modes": {
                    "run_a_stop_reasons": {},
                    "run_b_stop_reasons": {},
                },
                "missing_pairs": {"run_a_only_count": 0, "run_b_only_count": 0},
                "axis_comparison": {
                    "stability": {
                        "both_ok": 1,
                        "realism_only_ok": 0,
                        "effectiveness_only_ok": 0,
                        "neither_ok": 0,
                    },
                    "winner_alignment": {
                        "both_ok_count": 1,
                        "same": 0,
                        "different": 1,
                        "realism_a_effectiveness_b": 1,
                        "realism_b_effectiveness_a": 0,
                    },
                },
                "effectiveness": {
                    "scored_pairs": 1,
                    "order_inconsistent_pairs": 0,
                    "judge_failures": 0,
                    "order_consistency_rate": 1.0,
                    "low_confidence_pairs": 0,
                    "wins": {"A": 0, "B": 1, "tie": 0},
                    "confidence": {"high": 0, "medium": 1, "low": 0},
                    "by_dimension": {},
                    "outcomes": {
                        "policy_violation": {
                            "a_only": {"count": 0},
                            "b_only": {"count": 1},
                            "both": {"count": 0},
                            "neither": {"count": 0},
                            "unknown": {"count": 0},
                        },
                        "overrefusal": {
                            "a_only": {"count": 0},
                            "b_only": {"count": 0},
                            "both": {"count": 1},
                            "neither": {"count": 0},
                            "unknown": {"count": 0},
                        },
                    },
                    "by_behavior": {
                        "effectiveness-node": {
                            "wins": {"A": 0, "B": 1, "tie": 0},
                            "order_inconsistent": 0,
                            "judge_failures": 0,
                        }
                    },
                },
            }
        )

        self.assertIn("## Effectiveness Behavior Results", summary)
        self.assertIn("effectiveness-node: A=0, B=1, tie=0", summary)
        self.assertIn("## Axis Comparison", summary)
        self.assertIn("## Effectiveness Outcome Slices", summary)


class TesterPairwiseEvalRunTest(unittest.IsolatedAsyncioTestCase):
    def _test_case_row(self, *, test_case_id: str, behavior: str = "behavior") -> dict:
        return {
            "type": "scenario",
            "test_case_id": test_case_id,
            "behavior": "behavior",
            "dimensions": {"behavior": behavior},
            "seed": {
                "title": f"Title for {test_case_id}",
                "description": f"Scenario description for {test_case_id}.",
                "system_prompt": f"System prompt for {test_case_id}.",
            },
        }

    def _inference_row(
        self,
        *,
        test_case_id: str,
        run_label: str,
        behavior: str = "behavior",
        stop_reason: str = "max_turns",
    ) -> dict:
        return {
            "type": "scenario",
            "test_case_id": test_case_id,
            "behavior": "behavior",
            "dimensions": {"behavior": behavior},
            "target": "target-model",
            "tester_model": f"tester-{run_label}",
            "stop_reason": stop_reason,
            "events": [
                {
                    "view": ["target", "combined"],
                    "actor": "tester",
                    "edit": {
                        "type": "add_message",
                        "message": {
                            "role": "user",
                            "content": f"user turn from {run_label} for {test_case_id}",
                        },
                    },
                    "raw": None,
                },
                {
                    "view": ["target", "combined"],
                    "actor": "target",
                    "edit": {
                        "type": "add_message",
                        "message": {
                            "role": "assistant",
                            "content": f"assistant reply to {run_label} for {test_case_id}",
                        },
                    },
                    "raw": None,
                },
            ],
        }

    def _score_row(
        self,
        *,
        test_case_id: str,
        policy_violation: bool,
        overrefusal: bool,
        behavior: str = "behavior",
    ) -> dict:
        return {
            "type": "scenario",
            "test_case_id": test_case_id,
            "behavior": "behavior",
            "dimensions": {"behavior": behavior},
            "target": "target-model",
            "tester_model": "tester-model",
            "judge_model": "target-judge",
            "judge_status": "ok",
            "judge_error": None,
            "verdict": {
                "dimensions": {
                    "policy_violation": policy_violation,
                    "overrefusal": overrefusal,
                },
                "dimension_justifications": {
                    "policy_violation": "taxonomy",
                    "overrefusal": "overrefusal",
                },
                "justification": "overall",
                "node_judgments": [],
            },
        }

    def _write_jsonl(self, path: Path, rows: list[dict]) -> None:
        path.write_text(
            "".join(json.dumps(row) + "\n" for row in rows),
            encoding="utf-8",
        )

    def _write_run(
        self,
        suite_dir: Path,
        run_id: str,
        *,
        inference_rows: list[dict],
        score_rows: list[dict],
        judge_model: str = "saved-judge-model",
    ) -> Path:
        if not (suite_dir / "taxonomy.json").exists():
            behavior_names = sorted(
                {
                    row_behavior(row)
                    for row in [*inference_rows, *score_rows]
                    if row_behavior(row)
                }
            )
            (suite_dir / "taxonomy.json").write_text(
                json.dumps(
                    {
                        "behavior_categories": [
                            {"name": name, "definition": f"{name} definition", "permissible": False}
                            for name in behavior_names
                        ]
                    }
                ),
                encoding="utf-8",
            )
        run_dir = suite_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        self._write_jsonl(run_dir / "inference_set.jsonl", inference_rows)
        self._write_jsonl(run_dir / "scores.jsonl", score_rows)
        (run_dir / "config.yaml").write_text(
            yaml.safe_dump(
                {
                    "pipeline": {
                        "judge": {
                            "model": {"name": judge_model},
                        }
                    }
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        return run_dir

    async def test_run_pairwise_eval_aligns_shared_test_set_and_reports_unmatched(self) -> None:
        async def fake_generate_structured(model, prompt, *, schema_name, json_schema, options):
            del model, json_schema, options
            return ModelResponse(parsed=_verdict_for_schema_prompt(schema_name, prompt), text="{}", model="judge-model")

        with TemporaryDirectory() as tmp_dir:
            suite_dir = Path(tmp_dir) / "suite-a"
            suite_dir.mkdir(parents=True, exist_ok=True)
            self._write_jsonl(
                suite_dir / "test_set.jsonl",
                [
                    self._test_case_row(test_case_id="test-case-1"),
                    self._test_case_row(test_case_id="test-case-2"),
                    self._test_case_row(test_case_id="test-case-3"),
                ],
            )
            run_a = self._write_run(
                suite_dir,
                "run-a",
                inference_rows=[
                    self._inference_row(test_case_id="test-case-1", run_label="A"),
                    self._inference_row(test_case_id="test-case-2", run_label="A"),
                ],
                score_rows=[
                    self._score_row(test_case_id="test-case-1", policy_violation=False, overrefusal=False),
                    self._score_row(test_case_id="test-case-2", policy_violation=True, overrefusal=False),
                ],
            )
            run_b = self._write_run(
                suite_dir,
                "run-b",
                inference_rows=[
                    self._inference_row(test_case_id="test-case-2", run_label="B"),
                    self._inference_row(test_case_id="test-case-3", run_label="B"),
                ],
                score_rows=[
                    self._score_row(test_case_id="test-case-2", policy_violation=False, overrefusal=False),
                    self._score_row(test_case_id="test-case-3", policy_violation=False, overrefusal=True),
                ],
            )

            with (
                patch.object(tester_pairwise_eval, "generate_structured", new=fake_generate_structured),
                patch.object(tester_pairwise_eval, "generate", new=_eval_response),
            ):
                result = await tester_pairwise_eval.run_tester_pairwise_eval(
                    run_a_dir=str(run_a),
                    run_b_dir=str(run_b),
                    judge_model="judge-model",
                )

            rows = [
                json.loads(line)
                for line in Path(result["scores_path"]).read_text(encoding="utf-8").splitlines()
            ]
            metrics = json.loads(Path(result["metrics_path"]).read_text(encoding="utf-8"))

        self.assertEqual([row["test_case_id"] for row in rows], ["test-case-2"])
        self.assertEqual(metrics["total_matched_pairs"], 1)
        self.assertEqual(metrics["missing_pairs"]["run_a_only_count"], 1)
        self.assertEqual(metrics["missing_pairs"]["run_b_only_count"], 1)
        self.assertEqual(metrics["missing_pairs"]["run_a_only_test_case_ids"], ["test-case-1"])
        self.assertEqual(metrics["missing_pairs"]["run_b_only_test_case_ids"], ["test-case-3"])

    async def test_run_pairwise_eval_writes_expected_row_schema(self) -> None:
        async def fake_generate_structured(model, prompt, *, schema_name, json_schema, options):
            del model, json_schema, options
            return ModelResponse(
                parsed=_verdict_for_schema_prompt(
                    schema_name,
                    prompt,
                    realism_winner="B",
                    effectiveness_winner="A",
                ),
                text="{}",
                model="judge-model",
            )

        with TemporaryDirectory() as tmp_dir:
            suite_dir = Path(tmp_dir) / "suite-a"
            suite_dir.mkdir(parents=True, exist_ok=True)
            self._write_jsonl(suite_dir / "test_set.jsonl", [self._test_case_row(test_case_id="test-case-1")])
            run_a = self._write_run(
                suite_dir,
                "run-a",
                inference_rows=[self._inference_row(test_case_id="test-case-1", run_label="A")],
                score_rows=[self._score_row(test_case_id="test-case-1", policy_violation=True, overrefusal=False)],
            )
            run_b = self._write_run(
                suite_dir,
                "run-b",
                inference_rows=[self._inference_row(test_case_id="test-case-1", run_label="B")],
                score_rows=[self._score_row(test_case_id="test-case-1", policy_violation=False, overrefusal=True)],
            )

            with (
                patch.object(tester_pairwise_eval, "generate_structured", new=fake_generate_structured),
                patch.object(tester_pairwise_eval, "generate", new=_eval_response),
            ):
                result = await tester_pairwise_eval.run_tester_pairwise_eval(
                    run_a_dir=str(run_a),
                    run_b_dir=str(run_b),
                    judge_model="judge-model",
                )

            [row] = [
                json.loads(line)
                for line in Path(result["scores_path"]).read_text(encoding="utf-8").splitlines()
            ]

        self.assertEqual(row["judge_status"], "ok")
        self.assertEqual(row["winner"], "B")
        self.assertEqual(row["confidence"], "high")
        self.assertEqual(row["a_policy_violation"], True)
        self.assertEqual(row["b_overrefusal"], True)
        self.assertIn("dimension_winners", row)
        self.assertEqual(row["dimension_winners"]["realism"], "B")
        self.assertEqual(row["a_strengths"], ["More believable voice", "Better persona continuity"])
        self.assertEqual(row["effectiveness_winner"], "A")
        self.assertEqual(row["effectiveness_confidence"], "medium")
        self.assertEqual(row["effectiveness_dimension_winners"]["effectiveness"], "A")

    async def test_run_pairwise_eval_writes_judge_written_eval(self) -> None:
        calls: list[dict[str, object]] = []

        async def fake_generate_structured(model, prompt, *, schema_name, json_schema, options):
            del model, json_schema, options
            return ModelResponse(parsed=_verdict_for_schema_prompt(schema_name, prompt), text="{}", model="judge-model")

        async def fake_generate(model, prompt, *, options):
            calls.append({"model": model, "prompt": prompt, "max_tokens": options.max_tokens})
            return ModelResponse(text="Narrative interpretation.\n", model="judge-model")

        with TemporaryDirectory() as tmp_dir:
            suite_dir = Path(tmp_dir) / "suite-a"
            suite_dir.mkdir(parents=True, exist_ok=True)
            self._write_jsonl(suite_dir / "test_set.jsonl", [self._test_case_row(test_case_id="test-case-1")])
            run_a = self._write_run(
                suite_dir,
                "run-a",
                inference_rows=[self._inference_row(test_case_id="test-case-1", run_label="A")],
                score_rows=[self._score_row(test_case_id="test-case-1", policy_violation=False, overrefusal=False)],
            )
            run_b = self._write_run(
                suite_dir,
                "run-b",
                inference_rows=[self._inference_row(test_case_id="test-case-1", run_label="B")],
                score_rows=[self._score_row(test_case_id="test-case-1", policy_violation=True, overrefusal=False)],
            )

            with (
                patch.object(tester_pairwise_eval, "generate_structured", new=fake_generate_structured),
                patch.object(tester_pairwise_eval, "generate", new=fake_generate),
            ):
                result = await tester_pairwise_eval.run_tester_pairwise_eval(
                    run_a_dir=str(run_a),
                    run_b_dir=str(run_b),
                    judge_model="judge-model",
                )

            eval_path = Path(result["eval_path"])
            eval_exists = eval_path.exists()
            eval_text = eval_path.read_text(encoding="utf-8")
            meta_eval_path = Path(result["meta_eval_path"])
            meta_eval_exists = meta_eval_path.exists()

        self.assertEqual(len(calls), 2)
        eval_call = next(c for c in calls if c["max_tokens"] == tester_pairwise_eval.DEFAULT_EVAL_MAX_TOKENS)
        meta_eval_call = next(c for c in calls if c["max_tokens"] == tester_pairwise_eval.DEFAULT_META_EVAL_MAX_TOKENS)
        self.assertEqual(eval_call["model"], "judge-model")
        self.assertIn('"run_a": "run-a"', str(eval_call["prompt"]))
        self.assertIn('"run_b": "run-b"', str(eval_call["prompt"]))
        self.assertIn('"total_matched_pairs": 1', str(eval_call["prompt"]))
        self.assertTrue(eval_exists)
        self.assertIn("## Snapshot", eval_text)
        self.assertIn("- Matched pairs: 1", eval_text)
        self.assertIn("## Interpretation", eval_text)
        self.assertEqual(meta_eval_call["model"], "judge-model")
        self.assertTrue(meta_eval_exists)

    async def test_run_pairwise_eval_defaults_to_tmp_pairwise_dir_for_results_suite(self) -> None:
        async def fake_generate_structured(model, prompt, *, schema_name, json_schema, options):
            del model, json_schema, options
            return ModelResponse(parsed=_verdict_for_schema_prompt(schema_name, prompt), text="{}", model="judge-model")

        with TemporaryDirectory() as tmp_dir:
            suite_dir = Path(tmp_dir) / "artifacts" / "results" / "suite-a"
            suite_dir.mkdir(parents=True, exist_ok=True)
            self._write_jsonl(suite_dir / "test_set.jsonl", [self._test_case_row(test_case_id="test-case-1")])
            run_a = self._write_run(
                suite_dir,
                "run-a",
                inference_rows=[self._inference_row(test_case_id="test-case-1", run_label="A")],
                score_rows=[self._score_row(test_case_id="test-case-1", policy_violation=False, overrefusal=False)],
            )
            run_b = self._write_run(
                suite_dir,
                "run-b",
                inference_rows=[self._inference_row(test_case_id="test-case-1", run_label="B")],
                score_rows=[self._score_row(test_case_id="test-case-1", policy_violation=False, overrefusal=False)],
            )

            with (
                patch.object(tester_pairwise_eval, "generate_structured", new=fake_generate_structured),
                patch.object(tester_pairwise_eval, "generate", new=_eval_response),
            ):
                result = await tester_pairwise_eval.run_tester_pairwise_eval(
                    run_a_dir=str(run_a),
                    run_b_dir=str(run_b),
                    judge_model="judge-model",
                )

        expected_root = suite_dir.parent.parent / "tmp" / suite_dir.name / "pairwise"
        self.assertTrue(Path(result["summary_path"]).is_relative_to(expected_root))
        self.assertTrue(Path(result["eval_path"]).is_relative_to(expected_root))

    async def test_run_pairwise_eval_prompt_contains_both_transcripts_and_metadata(self) -> None:
        captured: list[tuple[str, str]] = []

        async def fake_generate_structured(model, prompt, *, schema_name, json_schema, options):
            del model, json_schema, options
            captured.append((schema_name, prompt))
            return ModelResponse(parsed=_verdict_for_schema_prompt(schema_name, prompt), text="{}", model="judge-model")

        with TemporaryDirectory() as tmp_dir:
            suite_dir = Path(tmp_dir) / "suite-a"
            suite_dir.mkdir(parents=True, exist_ok=True)
            self._write_jsonl(suite_dir / "test_set.jsonl", [self._test_case_row(test_case_id="test-case-1")])
            run_a = self._write_run(
                suite_dir,
                "run-a",
                inference_rows=[self._inference_row(test_case_id="test-case-1", run_label="A")],
                score_rows=[self._score_row(test_case_id="test-case-1", policy_violation=False, overrefusal=False)],
            )
            run_b = self._write_run(
                suite_dir,
                "run-b",
                inference_rows=[self._inference_row(test_case_id="test-case-1", run_label="B")],
                score_rows=[self._score_row(test_case_id="test-case-1", policy_violation=False, overrefusal=False)],
            )

            with (
                patch.object(tester_pairwise_eval, "generate_structured", new=fake_generate_structured),
                patch.object(tester_pairwise_eval, "generate", new=_eval_response),
            ):
                await tester_pairwise_eval.run_tester_pairwise_eval(
                    run_a_dir=str(run_a),
                    run_b_dir=str(run_b),
                    judge_model="judge-model",
                )

        self.assertEqual(len(captured), 4)
        realism_prompt = next(prompt for schema_name, prompt in captured if schema_name == "tester_pairwise_realism_judgment")
        effectiveness_prompt = next(prompt for schema_name, prompt in captured if schema_name == "tester_pairwise_effectiveness_judgment")
        self.assertIn("Seed ID: test-case-1", realism_prompt)
        self.assertIn("Scenario description for test-case-1.", realism_prompt)
        self.assertIn("user turn from A for test-case-1", realism_prompt)
        self.assertIn("assistant reply to A for test-case-1", realism_prompt)
        self.assertIn("user turn from B for test-case-1", realism_prompt)
        self.assertIn("assistant reply to B for test-case-1", realism_prompt)
        self.assertNotIn("Run:", realism_prompt)
        self.assertNotIn("Stop reason:", realism_prompt)
        self.assertNotIn("Visible user turns:", realism_prompt)
        self.assertIn("Judge realism, not effectiveness.", realism_prompt)
        self.assertIn("persona voice", realism_prompt)
        self.assertIn("interactional coherence", realism_prompt)
        self.assertNotIn("Judge effectiveness, not realism.", realism_prompt)
        self.assertNotIn("a_policy_violation", realism_prompt)
        self.assertNotIn("b_overrefusal", realism_prompt)
        self.assertIn("Judge effectiveness, not realism.", effectiveness_prompt)
        self.assertIn("elicitation craft", effectiveness_prompt)
        self.assertIn("evidence yield", effectiveness_prompt)
        self.assertNotIn("Judge realism, not effectiveness.", effectiveness_prompt)
        self.assertNotIn("a_policy_violation", effectiveness_prompt)
        self.assertNotIn("b_overrefusal", effectiveness_prompt)

    async def test_run_pairwise_eval_uses_saved_judge_model_when_omitted(self) -> None:
        captured: dict[str, object] = {}

        async def fake_generate_structured(model, prompt, *, schema_name, json_schema, options):
            del json_schema, options
            captured["model"] = model
            return ModelResponse(parsed=_verdict_for_schema_prompt(schema_name, prompt), text="{}", model="judge-model")

        with TemporaryDirectory() as tmp_dir:
            suite_dir = Path(tmp_dir) / "suite-a"
            suite_dir.mkdir(parents=True, exist_ok=True)
            self._write_jsonl(suite_dir / "test_set.jsonl", [self._test_case_row(test_case_id="test-case-1")])
            run_a = self._write_run(
                suite_dir,
                "run-a",
                inference_rows=[self._inference_row(test_case_id="test-case-1", run_label="A")],
                score_rows=[self._score_row(test_case_id="test-case-1", policy_violation=False, overrefusal=False)],
                judge_model="saved-pairwise-judge",
            )
            run_b = self._write_run(
                suite_dir,
                "run-b",
                inference_rows=[self._inference_row(test_case_id="test-case-1", run_label="B")],
                score_rows=[self._score_row(test_case_id="test-case-1", policy_violation=False, overrefusal=False)],
            )

            with (
                patch.object(tester_pairwise_eval, "generate_structured", new=fake_generate_structured),
                patch.object(tester_pairwise_eval, "generate", new=_eval_response),
            ):
                await tester_pairwise_eval.run_tester_pairwise_eval(
                    run_a_dir=str(run_a),
                    run_b_dir=str(run_b),
                )

        self.assertEqual(captured["model"], "saved-pairwise-judge")

    async def test_run_pairwise_eval_excludes_order_inconsistent_pairs(self) -> None:
        async def fake_generate_structured(model, prompt, *, schema_name, json_schema, options):
            del model, json_schema, options
            if schema_name == "tester_pairwise_effectiveness_judgment":
                return ModelResponse(parsed=_effectiveness_verdict(winner="A"), text="{}", model="judge-model")
            return ModelResponse(parsed=_pairwise_verdict(winner="A"), text="{}", model="judge-model")

        with TemporaryDirectory() as tmp_dir:
            suite_dir = Path(tmp_dir) / "suite-a"
            suite_dir.mkdir(parents=True, exist_ok=True)
            self._write_jsonl(suite_dir / "test_set.jsonl", [self._test_case_row(test_case_id="test-case-1")])
            run_a = self._write_run(
                suite_dir,
                "run-a",
                inference_rows=[self._inference_row(test_case_id="test-case-1", run_label="A")],
                score_rows=[self._score_row(test_case_id="test-case-1", policy_violation=False, overrefusal=False)],
            )
            run_b = self._write_run(
                suite_dir,
                "run-b",
                inference_rows=[self._inference_row(test_case_id="test-case-1", run_label="B")],
                score_rows=[self._score_row(test_case_id="test-case-1", policy_violation=False, overrefusal=False)],
            )

            with (
                patch.object(tester_pairwise_eval, "generate_structured", new=fake_generate_structured),
                patch.object(tester_pairwise_eval, "generate", new=_eval_response),
            ):
                result = await tester_pairwise_eval.run_tester_pairwise_eval(
                    run_a_dir=str(run_a),
                    run_b_dir=str(run_b),
                    judge_model="judge-model",
                )

            [row] = [
                json.loads(line)
                for line in Path(result["scores_path"]).read_text(encoding="utf-8").splitlines()
            ]
            metrics = json.loads(Path(result["metrics_path"]).read_text(encoding="utf-8"))

        self.assertEqual(row["judge_status"], "order_inconsistent")
        self.assertEqual(row["winner"], None)
        self.assertEqual(row["effectiveness_judge_status"], "order_inconsistent")
        self.assertEqual(metrics["scored_pairs"], 0)
        self.assertEqual(metrics["order_inconsistent_pairs"], 1)

    async def test_run_pairwise_eval_keeps_realism_when_only_effectiveness_is_order_inconsistent(self) -> None:
        async def fake_generate_structured(model, prompt, *, schema_name, json_schema, options):
            del model, json_schema, options
            if schema_name == "tester_pairwise_effectiveness_judgment":
                return ModelResponse(parsed=_effectiveness_verdict(winner="A"), text="{}", model="judge-model")
            return ModelResponse(parsed=_verdict_for_prompt(prompt, canonical_winner="B"), text="{}", model="judge-model")

        with TemporaryDirectory() as tmp_dir:
            suite_dir = Path(tmp_dir) / "suite-a"
            suite_dir.mkdir(parents=True, exist_ok=True)
            self._write_jsonl(suite_dir / "test_set.jsonl", [self._test_case_row(test_case_id="test-case-1")])
            run_a = self._write_run(
                suite_dir,
                "run-a",
                inference_rows=[self._inference_row(test_case_id="test-case-1", run_label="A")],
                score_rows=[self._score_row(test_case_id="test-case-1", policy_violation=False, overrefusal=False)],
            )
            run_b = self._write_run(
                suite_dir,
                "run-b",
                inference_rows=[self._inference_row(test_case_id="test-case-1", run_label="B")],
                score_rows=[self._score_row(test_case_id="test-case-1", policy_violation=False, overrefusal=False)],
            )

            with (
                patch.object(tester_pairwise_eval, "generate_structured", new=fake_generate_structured),
                patch.object(tester_pairwise_eval, "generate", new=_eval_response),
            ):
                result = await tester_pairwise_eval.run_tester_pairwise_eval(
                    run_a_dir=str(run_a),
                    run_b_dir=str(run_b),
                    judge_model="judge-model",
                )

            [row] = [
                json.loads(line)
                for line in Path(result["scores_path"]).read_text(encoding="utf-8").splitlines()
            ]
            metrics = json.loads(Path(result["metrics_path"]).read_text(encoding="utf-8"))

        self.assertEqual(row["judge_status"], "ok")
        self.assertEqual(row["winner"], "B")
        self.assertEqual(row["effectiveness_judge_status"], "order_inconsistent")
        self.assertEqual(row["effectiveness_winner"], None)
        self.assertEqual(metrics["scored_pairs"], 1)
        self.assertEqual(metrics["wins"], {"A": 0, "B": 1, "tie": 0})
        self.assertEqual(metrics["effectiveness"]["scored_pairs"], 0)
        self.assertEqual(metrics["effectiveness"]["order_inconsistent_pairs"], 1)

    async def test_run_pairwise_eval_keeps_effectiveness_when_only_realism_is_order_inconsistent(self) -> None:
        async def fake_generate_structured(model, prompt, *, schema_name, json_schema, options):
            del model, json_schema, options
            if schema_name == "tester_pairwise_effectiveness_judgment":
                return ModelResponse(parsed=_effectiveness_verdict_for_prompt(prompt, canonical_winner="B"), text="{}", model="judge-model")
            return ModelResponse(parsed=_pairwise_verdict(winner="A"), text="{}", model="judge-model")

        with TemporaryDirectory() as tmp_dir:
            suite_dir = Path(tmp_dir) / "suite-a"
            suite_dir.mkdir(parents=True, exist_ok=True)
            self._write_jsonl(suite_dir / "test_set.jsonl", [self._test_case_row(test_case_id="test-case-1")])
            run_a = self._write_run(
                suite_dir,
                "run-a",
                inference_rows=[self._inference_row(test_case_id="test-case-1", run_label="A")],
                score_rows=[self._score_row(test_case_id="test-case-1", policy_violation=False, overrefusal=False)],
            )
            run_b = self._write_run(
                suite_dir,
                "run-b",
                inference_rows=[self._inference_row(test_case_id="test-case-1", run_label="B")],
                score_rows=[self._score_row(test_case_id="test-case-1", policy_violation=True, overrefusal=False)],
            )

            with (
                patch.object(tester_pairwise_eval, "generate_structured", new=fake_generate_structured),
                patch.object(tester_pairwise_eval, "generate", new=_eval_response),
            ):
                result = await tester_pairwise_eval.run_tester_pairwise_eval(
                    run_a_dir=str(run_a),
                    run_b_dir=str(run_b),
                    judge_model="judge-model",
                )

            [row] = [
                json.loads(line)
                for line in Path(result["scores_path"]).read_text(encoding="utf-8").splitlines()
            ]
            metrics = json.loads(Path(result["metrics_path"]).read_text(encoding="utf-8"))

        self.assertEqual(row["judge_status"], "order_inconsistent")
        self.assertEqual(row["winner"], None)
        self.assertEqual(row["effectiveness_judge_status"], "ok")
        self.assertEqual(row["effectiveness_winner"], "B")
        self.assertEqual(metrics["scored_pairs"], 0)
        self.assertEqual(metrics["effectiveness"]["scored_pairs"], 1)
        self.assertEqual(metrics["effectiveness"]["wins"], {"A": 0, "B": 1, "tie": 0})
        self.assertEqual(metrics["axis_comparison"]["stability"]["effectiveness_only_ok"], 1)

    async def test_run_pairwise_eval_keeps_realism_when_only_effectiveness_judge_fails(self) -> None:
        async def fake_generate_structured(model, prompt, *, schema_name, json_schema, options):
            del model, json_schema, options
            if schema_name == "tester_pairwise_effectiveness_judgment":
                raise RuntimeError("effectiveness boom")
            return ModelResponse(parsed=_verdict_for_prompt(prompt, canonical_winner="A"), text="{}", model="judge-model")

        with TemporaryDirectory() as tmp_dir:
            suite_dir = Path(tmp_dir) / "suite-a"
            suite_dir.mkdir(parents=True, exist_ok=True)
            self._write_jsonl(suite_dir / "test_set.jsonl", [self._test_case_row(test_case_id="test-case-1")])
            run_a = self._write_run(
                suite_dir,
                "run-a",
                inference_rows=[self._inference_row(test_case_id="test-case-1", run_label="A")],
                score_rows=[self._score_row(test_case_id="test-case-1", policy_violation=False, overrefusal=False)],
            )
            run_b = self._write_run(
                suite_dir,
                "run-b",
                inference_rows=[self._inference_row(test_case_id="test-case-1", run_label="B")],
                score_rows=[self._score_row(test_case_id="test-case-1", policy_violation=False, overrefusal=False)],
            )

            with (
                patch.object(tester_pairwise_eval, "generate_structured", new=fake_generate_structured),
                patch.object(tester_pairwise_eval, "generate", new=_eval_response),
            ):
                result = await tester_pairwise_eval.run_tester_pairwise_eval(
                    run_a_dir=str(run_a),
                    run_b_dir=str(run_b),
                    judge_model="judge-model",
                )

            [row] = [
                json.loads(line)
                for line in Path(result["scores_path"]).read_text(encoding="utf-8").splitlines()
            ]
            metrics = json.loads(Path(result["metrics_path"]).read_text(encoding="utf-8"))

        self.assertEqual(row["judge_status"], "ok")
        self.assertEqual(row["winner"], "A")
        self.assertEqual(row["effectiveness_judge_status"], "judge_failed")
        self.assertEqual(row["effectiveness_judge_error"], "effectiveness boom")
        self.assertEqual(metrics["judge_failures"], 0)
        self.assertEqual(metrics["order_inconsistent_pairs"], 0)
        self.assertEqual(metrics["scored_pairs"], 1)
        self.assertEqual(metrics["effectiveness"]["judge_failures"], 1)
        self.assertEqual(metrics["effectiveness"]["scored_pairs"], 0)

    async def test_run_pairwise_eval_keeps_effectiveness_when_only_realism_judge_fails(self) -> None:
        async def fake_generate_structured(model, prompt, *, schema_name, json_schema, options):
            del model, json_schema, options
            if schema_name == "tester_pairwise_realism_judgment":
                raise RuntimeError("realism boom")
            return ModelResponse(parsed=_effectiveness_verdict_for_prompt(prompt, canonical_winner="A"), text="{}", model="judge-model")

        with TemporaryDirectory() as tmp_dir:
            suite_dir = Path(tmp_dir) / "suite-a"
            suite_dir.mkdir(parents=True, exist_ok=True)
            self._write_jsonl(suite_dir / "test_set.jsonl", [self._test_case_row(test_case_id="test-case-1")])
            run_a = self._write_run(
                suite_dir,
                "run-a",
                inference_rows=[self._inference_row(test_case_id="test-case-1", run_label="A")],
                score_rows=[self._score_row(test_case_id="test-case-1", policy_violation=False, overrefusal=False)],
            )
            run_b = self._write_run(
                suite_dir,
                "run-b",
                inference_rows=[self._inference_row(test_case_id="test-case-1", run_label="B")],
                score_rows=[self._score_row(test_case_id="test-case-1", policy_violation=False, overrefusal=True)],
            )

            with (
                patch.object(tester_pairwise_eval, "generate_structured", new=fake_generate_structured),
                patch.object(tester_pairwise_eval, "generate", new=_eval_response),
            ):
                result = await tester_pairwise_eval.run_tester_pairwise_eval(
                    run_a_dir=str(run_a),
                    run_b_dir=str(run_b),
                    judge_model="judge-model",
                )

            [row] = [
                json.loads(line)
                for line in Path(result["scores_path"]).read_text(encoding="utf-8").splitlines()
            ]
            metrics = json.loads(Path(result["metrics_path"]).read_text(encoding="utf-8"))

        self.assertEqual(row["judge_status"], "judge_failed")
        self.assertEqual(row["judge_error"], "realism boom")
        self.assertEqual(row["effectiveness_judge_status"], "ok")
        self.assertEqual(row["effectiveness_winner"], "A")
        self.assertEqual(metrics["judge_failures"], 1)
        self.assertEqual(metrics["scored_pairs"], 0)
        self.assertEqual(metrics["effectiveness"]["scored_pairs"], 1)
        self.assertEqual(metrics["axis_comparison"]["stability"]["effectiveness_only_ok"], 1)

    async def test_run_pairwise_eval_rejects_missing_seed_metadata(self) -> None:
        async def fake_generate_structured(model, prompt, *, schema_name, json_schema, options):
            del model, prompt, schema_name, json_schema, options
            raise AssertionError("judge should not run when test case metadata is missing")

        with TemporaryDirectory() as tmp_dir:
            suite_dir = Path(tmp_dir) / "suite-a"
            suite_dir.mkdir(parents=True, exist_ok=True)
            self._write_jsonl(suite_dir / "test_set.jsonl", [])
            run_a = self._write_run(
                suite_dir,
                "run-a",
                inference_rows=[self._inference_row(test_case_id="test-case-1", run_label="A")],
                score_rows=[self._score_row(test_case_id="test-case-1", policy_violation=False, overrefusal=False)],
            )
            run_b = self._write_run(
                suite_dir,
                "run-b",
                inference_rows=[self._inference_row(test_case_id="test-case-1", run_label="B")],
                score_rows=[self._score_row(test_case_id="test-case-1", policy_violation=False, overrefusal=False)],
            )

            with (
                patch.object(tester_pairwise_eval, "generate_structured", new=fake_generate_structured),
                patch.object(tester_pairwise_eval, "generate", new=_eval_response),
            ):
                with self.assertRaisesRegex(ValueError, "Missing test case metadata for shared test_case_id=test-case-1"):
                    await tester_pairwise_eval.run_tester_pairwise_eval(
                        run_a_dir=str(run_a),
                        run_b_dir=str(run_b),
                        judge_model="judge-model",
                    )

    async def test_run_pairwise_eval_marks_dimension_only_swap_mismatch_as_order_inconsistent(self) -> None:
        async def fake_generate_structured(model, prompt, *, schema_name, json_schema, options):
            del model, json_schema, options
            if schema_name == "tester_pairwise_effectiveness_judgment":
                verdict = _effectiveness_verdict_for_prompt(prompt, canonical_winner="A")
                prompt_is_swapped = prompt.index("user turn from B") < prompt.index("user turn from A")
                if prompt_is_swapped:
                    verdict = dict(verdict)
                    verdict["dimension_winners"] = dict(verdict["dimension_winners"])
                    verdict["dimension_winners"]["effectiveness"] = "A"
                return ModelResponse(parsed=verdict, text="{}", model="judge-model")
            return ModelResponse(parsed=_verdict_for_prompt(prompt, canonical_winner="A"), text="{}", model="judge-model")

        with TemporaryDirectory() as tmp_dir:
            suite_dir = Path(tmp_dir) / "suite-a"
            suite_dir.mkdir(parents=True, exist_ok=True)
            self._write_jsonl(suite_dir / "test_set.jsonl", [self._test_case_row(test_case_id="test-case-1")])
            run_a = self._write_run(
                suite_dir,
                "run-a",
                inference_rows=[self._inference_row(test_case_id="test-case-1", run_label="A")],
                score_rows=[self._score_row(test_case_id="test-case-1", policy_violation=False, overrefusal=False)],
            )
            run_b = self._write_run(
                suite_dir,
                "run-b",
                inference_rows=[self._inference_row(test_case_id="test-case-1", run_label="B")],
                score_rows=[self._score_row(test_case_id="test-case-1", policy_violation=False, overrefusal=False)],
            )

            with (
                patch.object(tester_pairwise_eval, "generate_structured", new=fake_generate_structured),
                patch.object(tester_pairwise_eval, "generate", new=_eval_response),
            ):
                result = await tester_pairwise_eval.run_tester_pairwise_eval(
                    run_a_dir=str(run_a),
                    run_b_dir=str(run_b),
                    judge_model="judge-model",
                )

            [row] = [
                json.loads(line)
                for line in Path(result["scores_path"]).read_text(encoding="utf-8").splitlines()
            ]
            metrics = json.loads(Path(result["metrics_path"]).read_text(encoding="utf-8"))

        self.assertEqual(row["judge_status"], "ok")
        self.assertEqual(row["effectiveness_judge_status"], "order_inconsistent")
        self.assertEqual(row["effectiveness_winner"], None)
        self.assertEqual(metrics["effectiveness"]["scored_pairs"], 0)
        self.assertEqual(metrics["effectiveness"]["order_inconsistent_pairs"], 1)


if __name__ == "__main__":
    unittest.main()
