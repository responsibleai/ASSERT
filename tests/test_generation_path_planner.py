import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = (
    ROOT
    / ".github"
    / "skills"
    / "assert-add-harm-eval-template"
    / "scripts"
    / "plan_generation_path.py"
)
SPEC = importlib.util.spec_from_file_location("plan_generation_path", SCRIPT_PATH)
assert SPEC and SPEC.loader
PLANNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PLANNER)


class GenerationPathPlannerTest(unittest.TestCase):
    def test_new_harm_uses_run_count_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "examples"
            root.mkdir()

            plan = PLANNER.plan_generation(
                eval_type="harm",
                name="violent_content",
                root=root,
                run_date="2026-08-13",
                runs=3,
            )

            self.assertFalse(plan["requires_confirmation"])
            self.assertFalse(plan["uses_date_suffix"])
            self.assertEqual(plan["runs"], 3)
            self.assertEqual(
                plan["proposed_directory"], str(root / "violent_content-3-runs")
            )

    def test_prior_yaml_is_detected_without_opening_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "examples"
            prior = root / "violent_content"
            prior.mkdir(parents=True)
            (prior / "eval_config.yaml").write_text("secret sentinel", encoding="utf-8")

            with (
                patch("builtins.open", side_effect=AssertionError("YAML content was opened")),
                patch.object(
                    Path,
                    "open",
                    side_effect=AssertionError("YAML content was opened"),
                ),
            ):
                plan = PLANNER.plan_generation(
                    eval_type="harm",
                    name="violent_content",
                    root=root,
                    run_date="2026-08-13",
                    runs=3,
                )

            self.assertTrue(plan["requires_confirmation"])
            self.assertEqual(
                plan["prior_generation_directories"][0]["yaml_file_count"], 1
            )
            self.assertEqual(
                plan["proposed_directory"],
                str(root / "violent_content_2026-08-13-3-runs"),
            )

    def test_same_day_collisions_receive_an_ordinal_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "examples"
            for name in (
                "violent_content",
                "violent_content_2026-08-13-3-runs",
                "violent_content_2026-08-13-3-runs_2",
            ):
                (root / name).mkdir(parents=True)
            (root / "violent_content" / "eval_config.yaml").touch()

            plan = PLANNER.plan_generation(
                eval_type="harm",
                name="violent_content",
                root=root,
                run_date="2026-08-13",
                runs=3,
            )

            self.assertEqual(
                plan["proposed_directory"],
                str(root / "violent_content_2026-08-13-3-runs_3"),
            )

    def test_system_generation_counts_nested_yaml_filenames(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "examples"
            system = root / "travel_agent"
            (system / "prompt_injection").mkdir(parents=True)
            (system / "privacy").mkdir()
            (system / "prompt_injection" / "eval_config.yaml").touch()
            (system / "privacy" / "eval_config.yml").touch()
            (root / "travel_agent_notes").mkdir()

            plan = PLANNER.plan_generation(
                eval_type="system",
                name="travel_agent",
                root=root,
                run_date="2026-08-13",
                runs=4,
            )

            self.assertEqual(len(plan["matching_paths"]), 1)
            self.assertEqual(
                plan["prior_generation_directories"][0]["yaml_file_count"], 2
            )
            self.assertEqual(
                plan["proposed_directory"],
                str(root / "travel_agent_2026-08-13-4-runs"),
            )

    def test_invalid_slug_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(PLANNER.GenerationPathError, "lowercase slug"):
                PLANNER.plan_generation(
                    eval_type="harm",
                    name="../violent_content",
                    root=Path(directory),
                    run_date="2026-08-13",
                    runs=3,
                )

    def test_invalid_run_count_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            for runs in (0, -1, True):
                with self.subTest(runs=runs):
                    with self.assertRaisesRegex(
                        PLANNER.GenerationPathError, "runs must be a positive integer"
                    ):
                        PLANNER.plan_generation(
                            eval_type="harm",
                            name="violent_content",
                            root=Path(directory),
                            run_date="2026-08-13",
                            runs=runs,
                        )


if __name__ == "__main__":
    unittest.main()