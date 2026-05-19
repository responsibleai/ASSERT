import asyncio
import unittest
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import patch

from p2m.core.config_model import DEFAULT_INFERENCE_MAX_TOKENS, EvaluationConfig, JudgeConfig, TargetConfig
from p2m.stages import judge, systematize, inference, test_set
from tests.helpers import StageSmokeCase, run_stage_smoke_case, write_json, write_jsonl


def _common_context(root: Path) -> dict[str, object]:
    return {
        "suite_id": "suite-1",
        "run_id": "run-1",
        "behavior": "Harmful advice",
        "behavior_name": "harmful_advice",
        "suite_root": root,
        "run_root": root / "run-1",
        "artifacts_root": root,
        "config_path": root / "config.yaml",
        "strict": False,
    }


def _seeds_case() -> StageSmokeCase:
    def cfg_factory(root: Path) -> dict[str, object]:
        return {
            "prompt": {"model": {"name": "azure/gpt-5.4"}, "sample_size": 10},
            "taxonomy_path": str(root / "taxonomy.json"),
            "save_path": str(root / "test_set.jsonl"),
        }

    def context_factory(root: Path) -> dict[str, object]:
        (root / "taxonomy.json").write_text(
            '{"behavior":{"name":"behavior"},"behavior_categories":[{"name":"b","definition":"d","examples":["e"],"permissible":false}]}',
            encoding="utf-8",
        )
        return _common_context(root)

    def result_factory(root: Path, _kwargs: dict[str, object]) -> dict[str, str]:
        out_path = root / "test_set.jsonl"
        out_path.write_text("", encoding="utf-8")
        return {"test_set_path": str(out_path)}

    def assert_fn(calls: dict[str, object], result: object, root: Path) -> None:
        assert calls["prompt"]["model"] == "azure/gpt-5.4"
        assert calls["prompt"]["sample_size"] == 10
        assert result["test_set_path"] == str(root / "test_set.jsonl")

    return StageSmokeCase(
        name="test_set",
        run=test_set.run,
        workflow_patch="p2m.stages.test_set.run_test_set",
        cfg_factory=cfg_factory,
        context_factory=context_factory,
        result_factory=result_factory,
        assert_fn=assert_fn,
    )


def _inference_case() -> StageSmokeCase:
    def setup_fn(root: Path) -> None:
        write_jsonl(root / "test_set.jsonl", [{"type": "prompt", "seed": {"description": "seed prompt"}}])

    def cfg_factory(root: Path) -> dict[str, object]:
        return {
            "test_set_path": str(root / "test_set.jsonl"),
            "save_dir": str(root),
            "strict": False,
        }

    def context_factory(root: Path) -> dict[str, object]:
        context = _common_context(root)
        context["target"] = TargetConfig(model="azure/gpt-5.4")
        context["evaluation"] = EvaluationConfig(judge=JudgeConfig(model="azure/gpt-5.4"))
        return context

    def result_factory(root: Path, _kwargs: dict[str, object]) -> dict[str, str]:
        transcripts = root / "transcripts.jsonl"
        transcripts.write_text("", encoding="utf-8")
        return {"transcripts_path": str(transcripts)}

    def assert_fn(calls: dict[str, object], result: object, root: Path) -> None:
        assert calls["target"].model == "azure/gpt-5.4"
        assert calls["max_tokens"] == DEFAULT_INFERENCE_MAX_TOKENS
        assert result["transcripts_path"] == str(root / "transcripts.jsonl")

    return StageSmokeCase(
        name="inference",
        run=inference.run,
        workflow_patch="p2m.stages.inference.run_inference",
        cfg_factory=cfg_factory,
        context_factory=context_factory,
        result_factory=result_factory,
        assert_fn=assert_fn,
        setup_fn=setup_fn,
    )


def _judge_case() -> StageSmokeCase:
    def setup_fn(root: Path) -> None:
        write_json(root / "taxonomy.json", {"behavior": {"name": "Risk"}, "behavior_categories": []})
        write_jsonl(root / "transcripts.jsonl", [{"type": "prompt", "test_case_id": "test-case-1"}])

    def cfg_factory(root: Path) -> dict[str, object]:
        return {
            "transcripts_path": str(root / "transcripts.jsonl"),
            "taxonomy_path": str(root / "taxonomy.json"),
            "save_dir": str(root),
            "strict": False,
        }

    def context_factory(root: Path) -> dict[str, object]:
        context = _common_context(root)
        context["target"] = TargetConfig(model="azure/gpt-5.4")
        context["evaluation"] = EvaluationConfig(judge=JudgeConfig(model="azure/gpt-5.4"))
        return context

    def result_factory(root: Path, _kwargs: dict[str, object]) -> dict[str, str]:
        scores = root / "scores.jsonl"
        scores.write_text("", encoding="utf-8")
        return {"scores_path": str(scores)}

    def assert_fn(calls: dict[str, object], result: object, root: Path) -> None:
        assert calls["evaluation"].judge.model == "azure/gpt-5.4"
        assert result["scores_path"] == str(root / "scores.jsonl")

    return StageSmokeCase(
        name="judge",
        run=judge.run,
        workflow_patch="p2m.stages.judge.run_judge",
        cfg_factory=cfg_factory,
        context_factory=context_factory,
        result_factory=result_factory,
        assert_fn=assert_fn,
        setup_fn=setup_fn,
    )


class StageRunnerSmokeTest(unittest.TestCase):
    def test_stage_runners_delegate_to_stage_functions(self) -> None:
        with self.subTest(stage="systematize"):
            with TemporaryDirectory() as tmp_dir:
                root = Path(tmp_dir)
                calls: dict[str, object] = {}

                async def fake_run_systematization(**kwargs: object) -> Path:
                    calls["systematization"] = kwargs
                    out_path = Path(str(kwargs["save_path"]))
                    out_path.write_text("{}", encoding="utf-8")
                    return out_path

                async def fake_run_systematization_to_policy(**kwargs: object) -> Path:
                    calls["convert"] = kwargs
                    out_path = Path(str(kwargs["save_path"]))
                    out_path.write_text("{}", encoding="utf-8")
                    return out_path

                with (
                    patch("p2m.stages.systematization.run_systematization", new=fake_run_systematization),
                    patch("p2m.stages.systematization_convert.run_systematization_to_policy", new=fake_run_systematization_to_policy),
                ):
                    result = asyncio.run(
                        systematize.run(
                            _common_context(root),
                            {
                                "model": {
                                    "name": "azure/gpt-5.4",
                                    "temperature": 0.0,
                                    "max_tokens": 800,
                                },
                                "behavior_category_count": 5,
                                "save_dir": str(root),
                            },
                        )
                    )

                self.assertEqual(calls["systematization"]["behavior"], "harmful_advice")
                self.assertEqual(calls["systematization"]["behavior_text"], "Harmful advice")
                self.assertEqual(calls["systematization"]["model_cfg"].name, "azure/gpt-5.4")
                self.assertEqual(calls["convert"]["behavior_category_count_hint"], 5)
                self.assertEqual(Path(result["taxonomy_path"]).resolve(), (root / "taxonomy.json").resolve())

        for case in [
            _seeds_case(),
            _inference_case(),
            _judge_case(),
        ]:
            with self.subTest(stage=case.name):
                run_stage_smoke_case(case)


if __name__ == "__main__":
    unittest.main()
