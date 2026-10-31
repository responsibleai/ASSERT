# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Judge input, cache, and real viewer artifacts must describe the same data."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from assert_ai.core.config_model import EvaluationConfig, InferenceConfig, JudgeConfig
from assert_ai.core.model_client import ModelResponse
from assert_ai.stages.judge import run_judge


class JudgeExternalInferenceTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.run_dir = self.root / "suite" / "run"
        self.run_dir.mkdir(parents=True)
        (self.run_dir / "config.yaml").write_text("pipeline: {}\n", encoding="utf-8")
        self.taxonomy = self.root / "taxonomy.json"
        self.taxonomy.write_text(
            json.dumps({"behavior_categories": [{
                "name": "helpful",
                "definition": "Answer the harmless synthetic request.",
                "permissible": True,
            }]}),
            encoding="utf-8",
        )
        self.source = self.root / "external" / "preserved-transcripts.jsonl"
        self.source.parent.mkdir()
        self.input_bytes = self._input_bytes("case-1", "Synthetic café answer.")
        self.source.write_bytes(self.input_bytes)
        self.canonical = self.run_dir / "inference_set.jsonl"
        self.evaluation = EvaluationConfig(
            judge=JudgeConfig(model="offline/judge"),
            inference=InferenceConfig(concurrency=1),
        )
        self.requests: list[str] = []
        self.during_response = None

        async def respond(model, messages, **kwargs):
            self.assertEqual(model, "offline/judge")
            self.requests.append(messages[1].content)
            if self.during_response is not None:
                self.during_response()
            verdict = {
                "dimensions": {"policy_violation": False, "overrefusal": False},
                "dimension_justifications": {
                    "policy_violation": "The harmless answer complies [1].",
                    "overrefusal": "The assistant answers the request [1].",
                },
                "node_judgments": [{
                    "node_name": "helpful",
                    "violated": False,
                    "confidence": "high",
                    "reasoning": "The synthetic answer is present [1].",
                }],
                "highlights": '<cite index="1"><part>Synthetic café answer.</part></cite>',
                "narrative": "The assistant provides the harmless synthetic answer.",
            }
            return ModelResponse(text=json.dumps(verdict), parsed=verdict)

        async def unexpected_transport(*args, **kwargs):
            raise AssertionError("No unstructured or live model transport is permitted")

        # Replace only the provider transport. Keep the real judge contract,
        # verdict normalization, cache, score writer, and viewer builder.
        for name, replacement in (
            ("generate_structured", respond),
            ("generate", unexpected_transport),
        ):
            transport = patch(f"assert_ai.core.judge.{name}", new=replacement)
            transport.start()
            self.addCleanup(transport.stop)

    @staticmethod
    def _input_bytes(case_id: str, content: str) -> bytes:
        row = {
            "type": "scenario",
            "test_case_id": case_id,
            "dimensions": {"behavior": "helpful"},
            "target": "synthetic-target",
            "tester_model": "synthetic-tester",
            "events": [{
                "view": ["target", "combined"],
                "actor": "target",
                "edit": {
                    "type": "add_message",
                    "message": {"role": "assistant", "content": content},
                },
            }],
        }
        # Non-ASCII text, CRLF, and blank lines expose accidental reserialization.
        return b"\r\n" + json.dumps(row, ensure_ascii=False).encode("utf-8") + b"\r\n\r\n"

    async def _run(self, source: Path | None = None):
        return await run_judge(
            inference_set_path=str(source or self.source),
            taxonomy_path=str(self.taxonomy),
            save_dir=str(self.run_dir),
            evaluation=self.evaluation,
        )

    def _assert_full_viewer(self) -> None:
        cache = self.run_dir / ".viewer"
        manifest = json.loads((cache / "viewer_run_manifest.json").read_text())
        self.assertEqual(
            manifest["source_files"]["inference_set.jsonl"]["path"],
            "inference_set.jsonl",
        )
        audit = json.loads((cache / "viewer_audit_rows.json").read_text())
        self.assertEqual([row["test_case_id"] for row in audit], ["case-1"])
        self.assertEqual(audit[0]["judge_status"], "ok")
        scores = [
            json.loads(line)
            for line in (self.run_dir / "scores.jsonl").read_text().splitlines()
        ]
        self.assertEqual([row["test_case_id"] for row in scores], ["case-1"])
        self.assertEqual(scores[0]["judge_status"], "ok")

    async def test_external_input_builds_viewer_without_changing_source_bytes(self):
        result = await self._run()

        self.assertEqual(result["new_count"], 1)
        self.assertEqual(self.source.read_bytes(), self.input_bytes)
        self.assertEqual(self.canonical.read_bytes(), self.input_bytes)
        self.assertEqual(len(self.requests), 1)
        self.assertIn("Synthetic café answer.", self.requests[0])
        self._assert_full_viewer()

    async def test_same_file_input_is_not_truncated_or_rewritten(self):
        self.canonical.write_bytes(self.input_bytes)

        result = await self._run(self.canonical)

        self.assertEqual(result["new_count"], 1)
        self.assertEqual(self.canonical.read_bytes(), self.input_bytes)
        self._assert_full_viewer()

    async def test_identical_existing_canonical_input_can_be_reused(self):
        self.canonical.write_bytes(self.input_bytes)

        await self._run()

        self.assertEqual(self.canonical.read_bytes(), self.input_bytes)
        self.assertEqual(self.source.read_bytes(), self.input_bytes)
        self._assert_full_viewer()

    async def test_conflicting_canonical_input_preserves_existing_evidence(self):
        stale = self._input_bytes("unrelated-case", "Unrelated preserved evidence.")
        self.canonical.write_bytes(stale)
        scores = self.run_dir / "scores.jsonl"
        previous_scores = b'{"type":"scenario","test_case_id":"unrelated-case"}\n'
        scores.write_bytes(previous_scores)
        fingerprint = self.run_dir / ".judge_config_hash"
        fingerprint.write_text("unrelated-evidence", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "canonical|inference"):
            await self._run()

        self.assertEqual(self.requests, [])
        self.assertEqual(self.source.read_bytes(), self.input_bytes)
        self.assertEqual(self.canonical.read_bytes(), stale)
        self.assertEqual(scores.read_bytes(), previous_scores)
        self.assertEqual(fingerprint.read_text(), "unrelated-evidence")

    async def test_cached_repeat_adds_no_scores_or_model_calls(self):
        await self._run()
        scores = self.run_dir / "scores.jsonl"
        first_scores = scores.read_bytes()

        result = await self._run()

        self.assertEqual(result["new_count"], 0)
        self.assertEqual(result["cached_count"], 1)
        self.assertEqual(scores.read_bytes(), first_scores)
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.canonical.read_bytes(), self.input_bytes)
        self._assert_full_viewer()

    async def test_source_change_during_judging_cannot_replace_scored_evidence(self):
        replacement = self._input_bytes("changed-case", "Different external input.")
        self.during_response = lambda: self.source.write_bytes(replacement)

        await self._run()

        self.assertEqual(self.source.read_bytes(), replacement)
        self.assertEqual(self.canonical.read_bytes(), self.input_bytes)
        self.assertIn("Synthetic café answer.", self.requests[0])
        self._assert_full_viewer()
        # The canonical captured input must reuse the same score fingerprint;
        # hashing a separately reread external file would force another call.
        result = await self._run(self.canonical)
        self.assertEqual(result["cached_count"], 1)
        self.assertEqual(result["new_count"], 0)
        self.assertEqual(len(self.requests), 1)
        self._assert_full_viewer()

    async def test_empty_external_input_leaves_existing_artifacts_unchanged(self):
        self.source.write_bytes(b"\r\n\n")
        self.canonical.write_bytes(self.input_bytes)

        with self.assertRaisesRegex(ValueError, "No inference rows"):
            await self._run()

        self.assertEqual(self.requests, [])
        self.assertEqual(self.source.read_bytes(), b"\r\n\n")
        self.assertEqual(self.canonical.read_bytes(), self.input_bytes)
        self.assertFalse((self.run_dir / "scores.jsonl").exists())

    async def test_changed_canonical_input_cannot_publish_mismatched_viewer(self):
        self.canonical.write_bytes(self.input_bytes)
        replacement = self._input_bytes("changed-case", "Different external input.")
        self.during_response = lambda: self.canonical.write_bytes(replacement)

        with self.assertRaisesRegex(ValueError, "changed|inference"):
            await self._run(self.canonical)

        self.assertEqual(self.canonical.read_bytes(), replacement)
        self.assertEqual(len(self.requests), 1)
        self.assertFalse((self.run_dir / ".viewer" / "viewer_run_manifest.json").exists())
