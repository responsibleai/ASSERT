import importlib.util
import json
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import yaml


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = (
    ROOT
    / ".github"
    / "skills"
    / "assert-add-harm-eval-template"
    / "scripts"
    / "validate_dimension_review.py"
)
SPEC = importlib.util.spec_from_file_location("validate_dimension_review", SCRIPT_PATH)
assert SPEC and SPEC.loader
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)


def _candidate(candidate_id: str, name: str, tags: list[str]) -> dict:
    return {
        "id": candidate_id,
        "name": name,
        "disposition": "keep",
        "citation_tags": tags,
    }


def _canonical(
    canonical_id: str,
    name: str,
    source_prefix: str,
    tags: list[str],
) -> dict:
    return {
        "id": canonical_id,
        "name": name,
        "purpose": f"Distinguish {name}",
        "levels_or_mode": "two evidence-backed levels",
        "observability": "multi-turn",
        "executable": True,
        "aliases": [],
        "source_items": [f"p1-{source_prefix}", f"p2-{source_prefix}"],
        "source_passes": [1, 2],
        "citation_tags": tags,
        "rationale": "Merged interchangeable findings from both passes.",
    }


def _valid_review(*, approved: bool = False) -> dict:
    passes = []
    for number in (1, 2):
        passes.append(
            {
                "number": number,
                "complete": True,
                "search_branches": [f"source branch {number}"],
                "breadth_audit_complete": True,
                "no_new_dimension_passes": 2,
                "candidates": {
                    "behavior_categories": [
                        _candidate(f"p{number}-behavior", "Boundary handling", ["[1]"])
                    ],
                    "test_dimensions": [
                        _candidate(
                            f"p{number}-test", "Interaction stage", ["[1]", "[2]"]
                        )
                    ],
                    "judge_dimensions": [
                        _candidate(
                            f"p{number}-judge", "Escalation quality", ["[1]", "[2]"]
                        )
                    ],
                },
            }
        )

    cycle_status = "approved" if approved else "pending_review"
    approval_status = "approved" if approved else "pending"
    return {
        "schema_version": 1,
        "harm_name": "example_harm",
        "n": 2,
        "active_cycle": "cycle-1",
        "references": {
            "[1]": {
                "title": "Primary source",
                "url": "https://example.com/primary",
                "accessed": "2026-08-13",
            },
            "[2]": {
                "title": "Independent source",
                "url": "https://example.com/independent",
                "accessed": "2026-08-13",
            },
        },
        "cycles": [
            {
                "id": "cycle-1",
                "criteria_version": "criteria-v1",
                "criteria": ["use realistic deployment settings"],
                "status": cycle_status,
                "passes": passes,
                "deduplication": {
                    "completed": True,
                    "duplicate_audit_complete": True,
                    "namespaces": {
                        "behavior_categories": [
                            _canonical(
                                "behavior-1", "Boundary handling", "behavior", ["[1]"]
                            )
                        ],
                        "test_dimensions": [
                            _canonical(
                                "test-1", "Interaction stage", "test", ["[1]", "[2]"]
                            )
                        ],
                        "judge_dimensions": [
                            _canonical(
                                "judge-1",
                                "Escalation quality",
                                "judge",
                                ["[1]", "[2]"],
                            )
                        ],
                    },
                    "rejections": [],
                },
            }
        ],
        "approval": {
            "status": approval_status,
            "cycle_id": "cycle-1",
            "criteria_version": "criteria-v1",
            "relevance": "approved" if approved else "pending",
            "edits": "none" if approved else "",
            "response": "Approved as shown" if approved else "",
            "approved_by": "user" if approved else "",
            "approved_at": datetime.now(timezone.utc).isoformat() if approved else None,
        },
    }


def _write_review(path: Path, data: dict) -> None:
    frontmatter = yaml.safe_dump(data, sort_keys=False)
    path.write_text(f"---\n{frontmatter}---\n", encoding="utf-8")
    VALIDATOR.render_review(path)


class DimensionReviewValidatorTest(unittest.TestCase):
    def test_pending_review_validates_but_cannot_open_write_gate(self) -> None:
        data = _valid_review()

        VALIDATOR.validate_review(data)
        with self.assertRaisesRegex(
            VALIDATOR.ReviewValidationError, "explicit user approval is required"
        ):
            VALIDATOR.validate_review(data, require_approval=True)

    def test_rejects_incomplete_pass_set_and_unresolved_citation(self) -> None:
        incomplete = _valid_review()
        incomplete["cycles"][0]["passes"].pop()
        with self.assertRaisesRegex(VALIDATOR.ReviewValidationError, "exactly n=2"):
            VALIDATOR.validate_review(incomplete)

        unresolved = _valid_review()
        unresolved["cycles"][0]["deduplication"]["namespaces"]["test_dimensions"][0][
            "citation_tags"
        ] = ["[1]", "[3]"]
        with self.assertRaisesRegex(VALIDATOR.ReviewValidationError, "undefined citation"):
            VALIDATOR.validate_review(unresolved)

    def test_rendered_body_must_match_frontmatter(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            review_path = Path(directory) / "dimension-review.md"
            _write_review(review_path, _valid_review())
            review_path.write_text(
                review_path.read_text(encoding="utf-8") + "stale\n", encoding="utf-8"
            )

            with self.assertRaisesRegex(VALIDATOR.ReviewValidationError, "body is stale"):
                VALIDATOR._validate_review_file(review_path, require_approval=False)

    def test_pre_and_post_write_stamp_proves_config_changed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            review_path = root / "dimension-review.md"
            config_path = root / "eval_config.yaml"
            stamp_path = root / "dimension-review.approval-stamp.json"
            _write_review(review_path, _valid_review(approved=True))

            VALIDATOR.pre_write(review_path, config_path, stamp_path)
            config_path.write_text("behavior:\n  name: example_harm\n", encoding="utf-8")
            VALIDATOR.post_write(review_path, config_path, stamp_path)

            stamp = json.loads(stamp_path.read_text(encoding="utf-8"))
            self.assertIn("post_write_verified_at", stamp)
            self.assertEqual(stamp["config_after_sha256"], VALIDATOR._sha256(config_path))

    def test_pre_write_rejects_existing_config_without_reading_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            review_path = root / "dimension-review.md"
            config_path = root / "eval_config.yaml"
            stamp_path = root / "dimension-review.approval-stamp.json"
            _write_review(review_path, _valid_review(approved=True))
            config_path.write_text("behavior:\n  name: existing\n", encoding="utf-8")

            with patch.object(
                VALIDATOR,
                "_sha256",
                side_effect=AssertionError("existing config content was read"),
            ) as sha256:
                with self.assertRaisesRegex(
                    VALIDATOR.ReviewValidationError, "config path already exists"
                ):
                    VALIDATOR.pre_write(review_path, config_path, stamp_path)

            sha256.assert_not_called()
            self.assertFalse(stamp_path.exists())

    def test_all_candidates_must_be_accounted_for(self) -> None:
        data = deepcopy(_valid_review())
        data["cycles"][0]["deduplication"]["namespaces"]["test_dimensions"][0][
            "source_items"
        ].pop()
        data["cycles"][0]["deduplication"]["namespaces"]["test_dimensions"][0][
            "source_passes"
        ] = [1]

        with self.assertRaisesRegex(VALIDATOR.ReviewValidationError, "does not account"):
            VALIDATOR.validate_review(data)


if __name__ == "__main__":
    unittest.main()