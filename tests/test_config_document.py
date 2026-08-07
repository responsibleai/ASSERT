# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

from pathlib import Path

import yaml

from assert_ai.core.config_document import (
    ConfigValidationCode,
    EvalConfigDocument,
    get_eval_config_json_schema,
    validate_eval_config_document,
)

ROOT = Path(__file__).resolve().parent.parent


def test_complete_document_validates() -> None:
    raw = {
        "suite": "demo-suite",
        "run": "run-1",
        "behavior": {
            "name": "safe_assistance",
            "description": "The assistant follows the behavior.",
        },
        "context": "A tool-using support agent.",
        "default_model": {
            "name": "azure/gpt-5.4",
            "reasoning_effort": "medium",
        },
        "artifacts_root": "artifacts",
        "results_dir": "artifacts/results",
        "pipeline": {
            "systematize": {
                "behavior_category_count": 10,
                "web_search": False,
            },
            "test_set": {
                "tool_source": "runtime",
                "prompt": {
                    "sample_size": 5,
                    "sampling": {
                        "method": "stratified",
                        "stratify_by": ["behavior"],
                    },
                },
                "scenario": {
                    "sample_size": 5,
                    "sampling": {
                        "method": "random",
                        "with_replacement": False,
                    },
                },
                "stratify": {
                    "dimensions": [
                        {
                            "name": "user_type",
                            "levels": [
                                {"name": "new", "definition": "A new user."},
                                {"name": "returning", "definition": "A returning user."},
                            ],
                        }
                    ]
                },
            },
            "inference": {
                "target": {
                    "callable": "agent:run",
                    "trace": {"backend": "phoenix", "group_by": "session.id"},
                },
                "tester": {"model": {"name": "azure/gpt-5.4-mini"}},
                "max_turns": 5,
                "concurrency": 2,
            },
            "judge": {
                "preset": ["policy", "quality"],
                "disabled_dimensions": ["overrefusal"],
                "dimensions": {
                    "response_quality": {
                        "description": "How strong was the response?",
                        "rubric": "Score overall response quality.",
                        "allow_not_applicable": True,
                        "scale": {
                            "type": "ordinal",
                            "values": {
                                1: "Poor",
                                2: "Acceptable",
                                3: "Strong",
                            },
                        },
                    }
                },
            },
        },
    }

    report = validate_eval_config_document(raw)

    assert report.valid is True
    assert report.issues == ()
    assert EvalConfigDocument.model_validate(raw).pipeline.inference is not None


def test_unknown_nested_field_has_stable_json_pointer() -> None:
    report = validate_eval_config_document(
        {
            "pipeline": {
                "inference": {
                    "target": {
                        "model": {"name": "azure/gpt-5.4"},
                        "legacy/type": "model",
                    }
                }
            }
        }
    )

    assert report.valid is False
    assert [issue.model_dump() for issue in report.issues] == [
        {
            "code": ConfigValidationCode.UNKNOWN_FIELD,
            "path": "/pipeline/inference/target/legacy~1type",
            "message": "Extra inputs are not permitted",
        }
    ]


def test_json_pointer_escapes_dynamic_judge_dimension_names() -> None:
    report = validate_eval_config_document(
        {
            "pipeline": {
                "judge": {
                    "dimensions": {
                        "quality/~strict": {
                            "description": "Quality",
                            "rubric": "Score quality",
                            "unexpected": True,
                        }
                    }
                }
            }
        }
    )

    assert report.valid is False
    assert report.issues[0].path == (
        "/pipeline/judge/dimensions/quality~1~0strict/unexpected"
    )


def test_generated_schema_is_versioned_and_strict() -> None:
    schema = get_eval_config_json_schema()

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["x-assert-schema-version"] == 1
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["pipeline"]
    pipeline_schema = schema["$defs"]["PipelineDocument"]
    assert pipeline_schema["additionalProperties"] is False
    assert pipeline_schema["minProperties"] == 1
    assert set(pipeline_schema["properties"]) == {
        "systematize",
        "test_set",
        "inference",
        "judge",
    }


def test_explicit_null_stage_is_rejected() -> None:
    report = validate_eval_config_document({"pipeline": {"judge": None}})

    assert report.valid is False
    assert report.issues[0].code == ConfigValidationCode.INVALID_TYPE
    assert report.issues[0].path == "/pipeline/judge"


def test_dimension_warning_threshold_is_not_a_schema_limit() -> None:
    report = validate_eval_config_document(
        {
            "pipeline": {
                "test_set": {
                    "stratify": {
                        "dimensions": [
                            {"name": f"dimension_{index}", "description": "Generated"}
                            for index in range(11)
                        ]
                    }
                }
            }
        }
    )

    assert report.valid is True


def test_all_customer_eval_configs_match_document_shape() -> None:
    paths = sorted((ROOT / "examples").glob("**/eval_config*.yaml"))
    paths.extend(sorted((ROOT / "examples").glob("**/behaviors/*.yaml")))
    assert paths

    failures: list[str] = []
    for path in paths:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        report = validate_eval_config_document(raw)
        if not report.valid:
            failures.append(
                f"{path.relative_to(ROOT)}: "
                + "; ".join(f"{issue.path}: {issue.message}" for issue in report.issues)
            )

    assert failures == []


def test_document_schema_fields_are_documented() -> None:
    docs = (ROOT / "docs" / "config" / "schema.md").read_text(encoding="utf-8")
    schema = get_eval_config_json_schema()

    for field_name in schema["properties"]:
        assert f"### `{field_name}`" in docs
    for stage_name in schema["$defs"]["PipelineDocument"]["properties"]:
        assert f"### `pipeline.{stage_name}`" in docs
