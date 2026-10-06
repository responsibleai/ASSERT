# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
import os
import subprocess
import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tests.node_runner import node_supports_ts, node_ts_args
from tests import test_viewer_server_artifacts as server_artifacts


ROOT = Path(__file__).resolve().parents[1]
TOKEN_USAGE_SRC = ROOT / "viewer" / "src" / "lib" / "token-usage.ts"
RUN_PAGE_SRC = (
    ROOT
    / "viewer"
    / "src"
    / "routes"
    / "suite"
    / "[suite_id]"
    / "[run_id]"
    / "+page.svelte"
)
EXPORT_PAGE_SRC = ROOT / "viewer" / "src" / "lib" / "export" / "ExportPage.svelte"
NEW_PAGE_SRC = ROOT / "viewer" / "src" / "routes" / "new" / "+page.svelte"
TOKEN_SUMMARY_SRC = (
    ROOT / "viewer" / "src" / "lib" / "components" / "TokenUsageSummary.svelte"
)
TOKEN_PREVIEW_SRC = (
    ROOT / "viewer" / "src" / "lib" / "components" / "TokenEstimatePreview.svelte"
)
ESTIMATE_ROUTE_SRC = (
    ROOT
    / "viewer"
    / "src"
    / "routes"
    / "api"
    / "runs"
    / "estimate"
    / "+server.ts"
)


class ViewerTokenUsageWiringTest(unittest.TestCase):
    def test_summary_is_wired_into_run_and_export_views(self) -> None:
        for path in (RUN_PAGE_SRC, EXPORT_PAGE_SRC):
            source = path.read_text(encoding="utf-8")
            self.assertIn("TokenUsageSummary", source)
            self.assertIn("tokenUsage={data.tokenUsage}", source)

    def test_wizard_shows_estimate_before_submit(self) -> None:
        page_source = NEW_PAGE_SRC.read_text(encoding="utf-8")
        route_source = ESTIMATE_ROUTE_SRC.read_text(encoding="utf-8")

        self.assertIn("TokenEstimatePreview estimate={tokenEstimate}", page_source)
        self.assertIn("Estimated token usage", TOKEN_PREVIEW_SRC.read_text(encoding="utf-8"))
        self.assertIn("fetch('/api/runs/estimate'", page_source)
        self.assertIn("estimateAssertAiRun", route_source)
        self.assertIn("request.signal", route_source)
        self.assertIn("No provider calls are", route_source)

    def test_completed_token_summary_is_compact(self) -> None:
        source = TOKEN_SUMMARY_SRC.read_text(encoding="utf-8")

        self.assertIn("<details", source)
        self.assertIn("In range", source)
        self.assertIn("displayActual.missingUsageCalls > 0", source)
        self.assertIn("'Reported' : 'Actual'", source)
        self.assertGreaterEqual(
            source.count("tokenAccuracyUnavailableMessage"),
            3,
        )
        self.assertNotIn("md:grid-cols-3", source)
        self.assertNotIn("text-2xl", source)


@unittest.skipUnless(node_supports_ts(), "node binary lacks TypeScript support (need >= 22.6)")
class ViewerTokenUsageFormattingTest(unittest.TestCase):
    def test_zero_note_only_and_legacy_metrics_normalization(self) -> None:
        helper = server_artifacts.ViewerServerArtifactsTest()
        with TemporaryDirectory(dir=ROOT / "viewer") as tmp_dir:
            root = Path(tmp_dir)
            harness = root / "harness"
            harness.mkdir()
            data_path = helper._copy_data_harness(harness)
            artifacts_root = root / "results"
            run_dir = artifacts_root / "suite-a" / "run-a"
            run_dir.mkdir(parents=True)
            (run_dir / "manifest.json").write_text(
                '{"status":"completed","stages":{"judge":"completed"}}', encoding="utf-8"
            )
            (run_dir / "config.yaml").write_text("pipeline: {}\n", encoding="utf-8")
            for file_name in ("inference_set.jsonl", "scores.jsonl"):
                (run_dir / file_name).write_text("", encoding="utf-8")
            helper._build_viewer_read_model(run_dir)
            caveat = "Opaque callable target usage is excluded."
            cases = {
                "zero": {"token_estimate": {
                    "calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
                    "lower_bound_tokens": 0, "upper_bound_tokens": 0,
                    "stages": {"inference": {"calls": 0, "total_tokens": 0}},
                    "notes": [caveat],
                }},
                "note_only": {"token_estimate": {"notes": [None, "", "  ", f" {caveat} "]}},
                "explicit_zero": {"token_estimate": {"total_tokens": 0}},
                "empty": {"token_estimate": {}},
                "invalid": {"token_estimate": {
                    "calls": -0.5, "total_tokens": "0", "notes": [False, {}, "  "],
                }},
                "legacy": {"totals": {"calls": 2, "input_tokens": 10, "output_tokens": 5}},
                "legacy_empty": {"totals": {"calls": 0, "input_tokens": 0}},
                "fallback_total": {"token_estimate": {
                    "input_tokens": 8, "output_tokens": 2, "stages": {"invalid": []},
                }},
                "asymmetric": {"token_estimate": {
                    "calls": 1, "total_tokens": 100,
                    "lower_bound_tokens": 65, "upper_bound_tokens": 4000,
                    "notes": ["Upper bound includes the full max_tool_calls cap."],
                }},
            }
            env = os.environ.copy()
            env.update({"ARTIFACTS_ROOT": str(artifacts_root), "MEASUREMENTS_ROOT": str(root)})
            script = textwrap.dedent(
                f"""\
                import fs from 'node:fs';
                const {{ loadRunPageData }} = await import({json.dumps(data_path.as_uri())});
                const {{ loadViewerRunReadModel }} = await import({json.dumps((harness / 'artifacts.ts').as_uri())});
                const cases = {json.dumps(cases)};
                const result = {{}};
                for (const [name, metrics] of Object.entries(cases)) {{
                  fs.writeFileSync({json.dumps(str(run_dir / 'metrics.json'))}, JSON.stringify(metrics));
                  loadViewerRunReadModel('suite-a', 'run-a');
                  result[name] = loadRunPageData('suite-a', 'run-a').tokenUsage;
                }}
                console.log(JSON.stringify(result));
                """
            )
            result = helper._run_node(harness_dir=harness, script=script, env=env)
            self.assertEqual(result.returncode, 0, msg=f"{result.stdout}\n{result.stderr}")
            payload = json.loads(result.stdout)
            for name in ("zero", "note_only", "explicit_zero"):
                with self.subTest(case=name):
                    self.assertIsNotNone(payload[name])
                    self.assertEqual(payload[name]["estimate"]["totalTokens"], 0)
                    self.assertEqual(payload[name]["estimate"]["lowerBoundTokens"], 0)
                    self.assertEqual(payload[name]["estimate"]["upperBoundTokens"], 0)
            self.assertEqual(payload["zero"]["estimate"]["notes"], [caveat])
            self.assertEqual(payload["note_only"]["estimate"]["notes"], [caveat])
            self.assertEqual(payload["zero"]["estimate"]["stages"]["inference"]["totalTokens"], 0)
            for name in ("empty", "invalid", "legacy_empty"):
                self.assertIsNone(payload[name], name)
            self.assertIsNone(payload["legacy"]["estimate"])
            self.assertEqual(payload["legacy"]["actual"]["totalTokens"], 15)
            self.assertEqual(payload["fallback_total"]["estimate"]["totalTokens"], 10)
            self.assertEqual(payload["asymmetric"]["estimate"]["totalTokens"], 100)
            self.assertEqual(payload["asymmetric"]["estimate"]["lowerBoundTokens"], 65)
            self.assertEqual(payload["asymmetric"]["estimate"]["upperBoundTokens"], 4000)
            self.assertEqual(
                payload["asymmetric"]["estimate"]["notes"],
                ["Upper bound includes the full max_tool_calls cap."],
            )

    def test_completed_summary_keeps_exclusion_visible_when_details_are_closed(self) -> None:
        with TemporaryDirectory(dir=ROOT / "viewer") as tmp_dir:
            compiled_path = Path(tmp_dir) / "summary.mjs"
            script = textwrap.dedent(
                f"""\
                import fs from 'node:fs';
                import {{ compile }} from 'svelte/compiler';
                import {{ render }} from 'svelte/server';
                const source = fs.readFileSync({json.dumps(str(TOKEN_SUMMARY_SRC))}, 'utf-8')
                  .replace('$lib/token-usage.js', {json.dumps(TOKEN_USAGE_SRC.as_uri())});
                const compiled = compile(source, {{ generate: 'server', filename: 'TokenUsageSummary.svelte' }});
                fs.writeFileSync({json.dumps(str(compiled_path))}, compiled.js.code);
                const {{ default: Summary }} = await import({json.dumps(compiled_path.as_uri())});
                const result = {{}};
                for (const total of [0, 100]) {{
                  result[total] = render(Summary, {{
                    props: {{ tokenUsage: {{
                      estimate: {{
                        calls: 0, inputTokens: total, outputTokens: 0, totalTokens: total,
                        lowerBoundTokens: total * 0.65, upperBoundTokens: total * 40, stages: {{}},
                        notes: [
                          'Opaque callable target usage is excluded.', 'A local heuristic estimate.',
                          'Upper bound includes the full max_tool_calls cap.'
                        ]
                      }},
                      actual: total ? {{
                        requests: 1, calls: 1, missingUsageCalls: 0,
                        inputTokens: 2900, outputTokens: 100, totalTokens: 3000,
                        cachedInputTokens: 0, cacheCreationInputTokens: 0,
                        cacheHitRate: 0, usageCoverage: 1
                      }} : null,
                      accuracy: total ? {{
                        status: 'available', actualTotalTokens: 3000, estimatedTotalTokens: 100,
                        differenceTokens: 2900, differenceRatio: 29, absolutePercentageError: 29
                      }} : null
                    }} }}
                  }}).body;
                }}
                console.log(JSON.stringify(result));
                """
            )
            result = subprocess.run(
                ["node", *node_ts_args(), "--input-type=module"],
                input=script, text=True, encoding="utf-8", capture_output=True,
                cwd=ROOT / "viewer", check=False,
            )
            self.assertEqual(result.returncode, 0, msg=f"{result.stdout}\n{result.stderr}")
            for total, html in json.loads(result.stdout).items():
                with self.subTest(total=total):
                    visible = html.split("<details", 1)[0]
                    self.assertIn("Opaque callable target usage is excluded.", visible)
                    self.assertIn("<details", html)
                    self.assertNotIn("<details open", html)
                    self.assertIn("A local heuristic estimate.", html)
                    self.assertIn("Upper bound includes the full max_tool_calls cap.", html)
                    if total == "0":
                        self.assertIn("total unknown", visible)
                        self.assertNotIn("~0", visible)
                    else:
                        self.assertIn("65–4K", visible)
                        self.assertIn("In range", visible)

    def test_resumed_run_compares_invocation_usage_and_shows_cumulative_total(self) -> None:
        helper = server_artifacts.ViewerServerArtifactsTest()
        with TemporaryDirectory(dir=ROOT / "viewer") as tmp_dir:
            root = Path(tmp_dir)
            harness = root / "harness"
            harness.mkdir()
            data_path = helper._copy_data_harness(harness)
            artifacts_root = root / "results"
            run_dir = artifacts_root / "suite-a" / "run-a"
            run_dir.mkdir(parents=True)
            (run_dir / "manifest.json").write_text(
                '{"status":"completed","stages":{"judge":"completed"}}',
                encoding="utf-8",
            )
            (run_dir / "config.yaml").write_text("pipeline: {}\n", encoding="utf-8")
            for file_name in ("inference_set.jsonl", "scores.jsonl"):
                (run_dir / file_name).write_text("", encoding="utf-8")
            helper._build_viewer_read_model(run_dir)
            (run_dir / "metrics.json").write_text(
                json.dumps(
                    {
                        "token_estimate": {
                            "calls": 1,
                            "input_tokens": 90,
                            "output_tokens": 20,
                            "total_tokens": 110,
                            "lower_bound_tokens": 90,
                            "upper_bound_tokens": 120,
                            "stages": {
                                "judge": {
                                    "calls": 1,
                                    "total_tokens": 110,
                                }
                            },
                        },
                        "token_estimate_scope": "current_invocation",
                        "totals": {
                            "requests": 11,
                            "calls": 11,
                            "input_tokens": 9_000,
                            "output_tokens": 1_100,
                            "total_tokens": 10_100,
                        },
                        "invocation": {
                            "totals": {
                                "requests": 1,
                                "calls": 1,
                                "input_tokens": 80,
                                "output_tokens": 20,
                                "total_tokens": 100,
                            }
                        },
                        "token_estimate_accuracy": {
                            "status": "available",
                            "actual_total_tokens": 100,
                            "estimated_total_tokens": 110,
                            "difference_tokens": -10,
                            "difference_ratio": -10 / 110,
                            "absolute_percentage_error": 10 / 110,
                        },
                    }
                ),
                encoding="utf-8",
            )
            compiled_path = root / "summary.mjs"
            env = os.environ.copy()
            env.update(
                {
                    "ARTIFACTS_ROOT": str(artifacts_root),
                    "MEASUREMENTS_ROOT": str(root),
                }
            )
            script = textwrap.dedent(
                f"""\
                import fs from 'node:fs';
                import {{ compile }} from 'svelte/compiler';
                import {{ render }} from 'svelte/server';
                const {{ loadRunPageData }} = await import({json.dumps(data_path.as_uri())});
                const {{ loadViewerRunReadModel }} = await import(
                  {json.dumps((harness / 'artifacts.ts').as_uri())}
                );
                loadViewerRunReadModel('suite-a', 'run-a');
                const tokenUsage = loadRunPageData('suite-a', 'run-a').tokenUsage;
                const source = fs.readFileSync(
                  {json.dumps(str(TOKEN_SUMMARY_SRC))},
                  'utf-8'
                ).replace(
                  '$lib/token-usage.js',
                  {json.dumps(TOKEN_USAGE_SRC.as_uri())}
                );
                const compiled = compile(source, {{
                  generate: 'server',
                  filename: 'TokenUsageSummary.svelte'
                }});
                fs.writeFileSync({json.dumps(str(compiled_path))}, compiled.js.code);
                const {{ default: Summary }} = await import(
                  {json.dumps(compiled_path.as_uri())}
                );
                console.log(JSON.stringify({{
                  tokenUsage,
                  html: render(Summary, {{ props: {{ tokenUsage }} }}).body
                }}));
                """
            )
            result = helper._run_node(harness_dir=harness, script=script, env=env)

        self.assertEqual(result.returncode, 0, msg=f"{result.stdout}\n{result.stderr}")
        payload = json.loads(result.stdout)
        token_usage = payload["tokenUsage"]
        self.assertEqual(token_usage["actual"]["totalTokens"], 10_100)
        self.assertEqual(token_usage["invocationActual"]["totalTokens"], 100)
        self.assertEqual(token_usage["estimateActual"]["totalTokens"], 100)
        visible = payload["html"].split("<details", 1)[0]
        self.assertIn("Estimated this invocation", visible)
        self.assertIn("This invocation Actual", visible)
        self.assertIn("Cumulative", visible)
        self.assertIn("10.1K", visible)
        self.assertIn("In range", visible)
        self.assertNotIn("Outside range", visible)

    def test_resumed_run_displays_current_usage_with_prior_estimate(self) -> None:
        helper = server_artifacts.ViewerServerArtifactsTest()
        with TemporaryDirectory(dir=ROOT / "viewer") as tmp_dir:
            root = Path(tmp_dir)
            harness = root / "harness"
            harness.mkdir()
            data_path = helper._copy_data_harness(harness)
            artifacts_root = root / "results"
            run_dir = artifacts_root / "suite-a" / "run-a"
            run_dir.mkdir(parents=True)
            (run_dir / "manifest.json").write_text(
                '{"status":"completed","stages":{"judge":"completed"}}',
                encoding="utf-8",
            )
            (run_dir / "config.yaml").write_text("pipeline: {}\n", encoding="utf-8")
            for file_name in ("inference_set.jsonl", "scores.jsonl"):
                (run_dir / file_name).write_text("", encoding="utf-8")
            helper._build_viewer_read_model(run_dir)
            (run_dir / "metrics.json").write_text(
                json.dumps(
                    {
                        "token_estimate": {
                            "calls": 1,
                            "input_tokens": 900,
                            "output_tokens": 100,
                            "total_tokens": 1_000,
                            "lower_bound_tokens": 900,
                            "upper_bound_tokens": 1_100,
                            "stages": {
                                "judge": {
                                    "calls": 1,
                                    "total_tokens": 1_000,
                                }
                            },
                        },
                        "token_estimate_scope": "prior_invocation",
                        "totals": {
                            "requests": 11,
                            "calls": 11,
                            "input_tokens": 9_000,
                            "output_tokens": 1_100,
                            "total_tokens": 10_100,
                        },
                        "invocation": {
                            "totals": {
                                "requests": 1,
                                "calls": 1,
                                "input_tokens": 80,
                                "output_tokens": 20,
                                "total_tokens": 100,
                            }
                        },
                        "token_estimate_accuracy": {
                            "status": "unavailable",
                            "reason": "estimate_scope_mismatch",
                        },
                    }
                ),
                encoding="utf-8",
            )
            compiled_path = root / "summary-prior.mjs"
            env = os.environ.copy()
            env.update(
                {
                    "ARTIFACTS_ROOT": str(artifacts_root),
                    "MEASUREMENTS_ROOT": str(root),
                }
            )
            script = textwrap.dedent(
                f"""\
                import fs from 'node:fs';
                import {{ compile }} from 'svelte/compiler';
                import {{ render }} from 'svelte/server';
                const {{ loadRunPageData }} = await import({json.dumps(data_path.as_uri())});
                const {{ loadViewerRunReadModel }} = await import(
                  {json.dumps((harness / 'artifacts.ts').as_uri())}
                );
                loadViewerRunReadModel('suite-a', 'run-a');
                const tokenUsage = loadRunPageData('suite-a', 'run-a').tokenUsage;
                const source = fs.readFileSync(
                  {json.dumps(str(TOKEN_SUMMARY_SRC))},
                  'utf-8'
                ).replace(
                  '$lib/token-usage.js',
                  {json.dumps(TOKEN_USAGE_SRC.as_uri())}
                );
                const compiled = compile(source, {{
                  generate: 'server',
                  filename: 'TokenUsageSummary.svelte'
                }});
                fs.writeFileSync({json.dumps(str(compiled_path))}, compiled.js.code);
                const {{ default: Summary }} = await import(
                  {json.dumps(compiled_path.as_uri())}
                );
                console.log(JSON.stringify({{
                  tokenUsage,
                  html: render(Summary, {{ props: {{ tokenUsage }} }}).body
                }}));
                """
            )
            result = helper._run_node(harness_dir=harness, script=script, env=env)

        self.assertEqual(result.returncode, 0, msg=f"{result.stdout}\n{result.stderr}")
        payload = json.loads(result.stdout)
        token_usage = payload["tokenUsage"]
        self.assertEqual(token_usage["estimateScope"], "prior_invocation")
        self.assertEqual(token_usage["actual"]["totalTokens"], 10_100)
        self.assertEqual(token_usage["invocationActual"]["totalTokens"], 100)
        self.assertIsNone(token_usage["estimateActual"])
        visible = payload["html"].split("<details", 1)[0]
        self.assertIn("Prior invocation estimate", visible)
        self.assertNotIn("Estimated this invocation", visible)
        self.assertIn("This invocation Actual", visible)
        self.assertNotIn("Actual unavailable", visible)
        self.assertIn("Cumulative", visible)
        self.assertIn("10.1K", visible)
        self.assertNotIn("In range", visible)
        self.assertNotIn("Outside range", visible)

    def test_explicit_zero_invocation_and_legacy_metrics_render_distinctly(self) -> None:
        helper = server_artifacts.ViewerServerArtifactsTest()
        with TemporaryDirectory(dir=ROOT / "viewer") as tmp_dir:
            root = Path(tmp_dir)
            harness = root / "harness"
            harness.mkdir()
            data_path = helper._copy_data_harness(harness)
            artifacts_root = root / "results"
            run_dir = artifacts_root / "suite-a" / "run-a"
            run_dir.mkdir(parents=True)
            (run_dir / "manifest.json").write_text(
                '{"status":"completed","stages":{"judge":"completed"}}',
                encoding="utf-8",
            )
            (run_dir / "config.yaml").write_text("pipeline: {}\n", encoding="utf-8")
            for file_name in ("inference_set.jsonl", "scores.jsonl"):
                (run_dir / file_name).write_text("", encoding="utf-8")
            helper._build_viewer_read_model(run_dir)
            compiled_path = root / "summary-zero.mjs"
            env = os.environ.copy()
            env.update(
                {
                    "ARTIFACTS_ROOT": str(artifacts_root),
                    "MEASUREMENTS_ROOT": str(root),
                }
            )
            metrics_cases = {
                "explicit_zero": {
                    "token_estimate": {
                        "calls": 1,
                        "total_tokens": 900,
                        "lower_bound_tokens": 800,
                        "upper_bound_tokens": 1_000,
                    },
                    "token_estimate_scope": "prior_invocation",
                    "token_estimate_accuracy": {
                        "status": "unavailable",
                        "reason": "estimate_scope_mismatch",
                    },
                    "totals": {
                        "requests": 11,
                        "calls": 11,
                        "input_tokens": 9_000,
                        "output_tokens": 1_100,
                        "total_tokens": 10_100,
                    },
                    "invocation": {
                        "totals": {
                            "requests": 0,
                            "calls": 0,
                            "input_tokens": 0,
                            "output_tokens": 0,
                            "total_tokens": 0,
                        }
                    },
                },
                "legacy": {
                    "totals": {
                        "requests": 11,
                        "calls": 11,
                        "input_tokens": 9_000,
                        "output_tokens": 1_100,
                        "total_tokens": 10_100,
                    }
                },
            }
            script = textwrap.dedent(
                f"""\
                import fs from 'node:fs';
                import {{ compile }} from 'svelte/compiler';
                import {{ render }} from 'svelte/server';
                const {{ loadRunPageData }} = await import({json.dumps(data_path.as_uri())});
                const {{ loadViewerRunReadModel }} = await import(
                  {json.dumps((harness / 'artifacts.ts').as_uri())}
                );
                loadViewerRunReadModel('suite-a', 'run-a');
                const source = fs.readFileSync(
                  {json.dumps(str(TOKEN_SUMMARY_SRC))},
                  'utf-8'
                ).replace(
                  '$lib/token-usage.js',
                  {json.dumps(TOKEN_USAGE_SRC.as_uri())}
                );
                const compiled = compile(source, {{
                  generate: 'server',
                  filename: 'TokenUsageSummary.svelte'
                }});
                fs.writeFileSync({json.dumps(str(compiled_path))}, compiled.js.code);
                const {{ default: Summary }} = await import(
                  {json.dumps(compiled_path.as_uri())}
                );
                const metricsCases = {json.dumps(metrics_cases)};
                const result = {{}};
                for (const [name, metrics] of Object.entries(metricsCases)) {{
                  fs.writeFileSync(
                    {json.dumps(str(run_dir / 'metrics.json'))},
                    JSON.stringify(metrics)
                  );
                  const tokenUsage = loadRunPageData('suite-a', 'run-a').tokenUsage;
                  result[name] = {{
                    tokenUsage,
                    html: render(Summary, {{ props: {{ tokenUsage }} }}).body
                  }};
                }}
                console.log(JSON.stringify(result));
                """
            )
            result = helper._run_node(harness_dir=harness, script=script, env=env)

        self.assertEqual(result.returncode, 0, msg=f"{result.stdout}\n{result.stderr}")
        payload = json.loads(result.stdout)
        explicit = payload["explicit_zero"]
        self.assertEqual(explicit["tokenUsage"]["invocationActual"]["totalTokens"], 0)
        self.assertIsNone(explicit["tokenUsage"]["estimateActual"])
        explicit_visible = explicit["html"].split("<details", 1)[0]
        self.assertIn("Prior invocation estimate", explicit_visible)
        self.assertIn("Current invocation usage unavailable", explicit_visible)
        self.assertIn("Cumulative", explicit_visible)
        self.assertIn("10.1K", explicit_visible)
        self.assertNotIn("This invocation Actual", explicit_visible)

        legacy = payload["legacy"]
        self.assertIsNone(legacy["tokenUsage"]["invocationActual"])
        self.assertEqual(legacy["tokenUsage"]["actual"]["totalTokens"], 10_100)
        legacy_visible = legacy["html"].split("<details", 1)[0]
        self.assertIn("Actual", legacy_visible)
        self.assertIn("10.1K", legacy_visible)
        self.assertNotIn("Current invocation usage unavailable", legacy_visible)
        self.assertNotIn("Cumulative", legacy_visible)

    def test_preview_renders_zero_and_nonzero_exclusions_and_legacy_estimates(self) -> None:
        with TemporaryDirectory(dir=ROOT / "viewer") as tmp_dir:
            compiled_path = Path(tmp_dir) / "preview.mjs"
            script = textwrap.dedent(
                f"""\
                import fs from 'node:fs';
                import {{ compile }} from 'svelte/compiler';
                import {{ render }} from 'svelte/server';
                const source = fs.readFileSync({json.dumps(str(TOKEN_PREVIEW_SRC))}, 'utf-8')
                  .replace('$lib/token-usage.js', {json.dumps(TOKEN_USAGE_SRC.as_uri())});
                const compiled = compile(source, {{ generate: 'server', filename: 'TokenEstimatePreview.svelte' }});
                fs.writeFileSync({json.dumps(str(compiled_path))}, compiled.js.code);
                const {{ default: Preview }} = await import({json.dumps(compiled_path.as_uri())});
                const result = {{}};
                for (const target of ['callable', 'endpoint', 'sandbox']) {{
                  for (const total of [0, 100]) {{
                    result[`${{target}}-${{total}}`] = render(Preview, {{
                      props: {{ estimate: {{
                        calls: total ? 1 : 0, input_tokens: total, output_tokens: 0, total_tokens: total,
                        lower_bound_tokens: total * 0.65, upper_bound_tokens: total * 40,
                        notes: [
                          `Opaque ${{target}} target usage is excluded.`, 'A local heuristic estimate.',
                          'Upper bound includes the full max_tool_calls cap.'
                        ]
                      }} }}
                    }}).body;
                  }}
                }}
                result.legacy = render(Preview, {{ props: {{ estimate: {{
                  calls: 0, input_tokens: 0, output_tokens: 0, total_tokens: 0,
                  lower_bound_tokens: 0, upper_bound_tokens: 0
                }} }} }}).body;
                result.noop = render(Preview, {{ props: {{ estimate: {{
                  calls: 0, input_tokens: 0, output_tokens: 0, total_tokens: 0,
                  lower_bound_tokens: 0, upper_bound_tokens: 0,
                  notes: ['Retries and provider-side hidden overhead are not included.']
                }} }} }}).body;
                result.loading = render(Preview, {{ props: {{ estimate: null, loading: true }} }}).body;
                result.error = render(Preview, {{ props: {{ estimate: null, error: 'Unavailable' }} }}).body;
                console.log(JSON.stringify(result));
                """
            )
            result = subprocess.run(
                ["node", *node_ts_args(), "--input-type=module"],
                input=script, text=True, encoding="utf-8", capture_output=True,
                cwd=ROOT / "viewer", check=False,
            )
            self.assertEqual(result.returncode, 0, msg=f"{result.stdout}\n{result.stderr}")
            payload = json.loads(result.stdout)
            for target in ("callable", "endpoint", "sandbox"):
                for total in (0, 100):
                    with self.subTest(target=target, total=total):
                        html = payload[f"{target}-{total}"]
                        visible = html.split("<details", 1)[0]
                        self.assertIn(f"Opaque {target} target usage is excluded.", visible)
                        self.assertIn("A local heuristic estimate.", html)
                        self.assertIn("Upper bound includes the full max_tool_calls cap.", html)
                        self.assertNotIn("<details open", html)
                        if total == 0:
                            self.assertIn("0 known tokens", visible)
                            self.assertIn("total unknown", visible)
                            self.assertNotIn("~0", visible)
                        else:
                            self.assertIn("~100", visible)
                            self.assertIn("Range 65–4K", visible)
                            self.assertIn("partial estimate", visible)
            self.assertIn("~0", payload["legacy"])
            self.assertNotIn("total unknown", payload["legacy"])
            self.assertIn("~0", payload["noop"])
            self.assertNotIn("total unknown", payload["noop"])
            self.assertNotIn("partial estimate", payload["noop"])
            self.assertNotIn(
                "Retries and provider-side hidden overhead",
                payload["noop"].split("<details", 1)[0],
            )
            self.assertIn("Estimating", payload["loading"])
            self.assertIn("Estimate unavailable", payload["error"])

    def test_formats_comparison_and_range_states(self) -> None:
        script = textwrap.dedent(
            f"""\
            const helpers = await import({json.dumps(TOKEN_USAGE_SRC.as_uri())});
            console.log(JSON.stringify({{
              small: helpers.formatTokenCount(999),
              thousands: helpers.formatTokenCount(3089),
              millions: helpers.formatTokenCount(1250000),
              lower: helpers.formatActualVsEstimate(-0.179),
              higher: helpers.formatActualVsEstimate(0.072),
              matched: helpers.formatActualVsEstimate(0),
              matchedSentence: helpers.actualVsEstimateSentence(0),
              within: helpers.actualIsWithinEstimate(2536, {{
                lowerBoundTokens: 2162,
                upperBoundTokens: 4016
              }}),
              outside: helpers.actualIsWithinEstimate(4500, {{
                lowerBoundTokens: 2162,
                upperBoundTokens: 4016
              }}),
              incomplete: helpers.tokenAccuracyUnavailableMessage(
                'provider_usage_incomplete',
                0.5
              ),
              stage: helpers.tokenStageLabel('test_set'),
              notes: helpers.splitTokenEstimateNotes([
                null, '', '  ', ' Opaque callable target usage is excluded. ',
                'Endpoint usage cannot be estimated.',
                'Sandbox usage is not included.',
                'Exclusions: unobservable target calls.',
                'A local heuristic estimate.'
              ])
            }}));
            """
        )
        result = subprocess.run(
            ["node", *node_ts_args(), "--input-type=module"],
            input=script,
            text=True,
            capture_output=True,
            cwd=ROOT / "viewer",
            check=False,
        )

        self.assertEqual(result.returncode, 0, msg=f"{result.stdout}\n{result.stderr}")
        payload = json.loads(result.stdout)
        self.assertEqual(payload["small"], "999")
        self.assertEqual(payload["thousands"], "3.1K")
        self.assertEqual(payload["millions"], "1.3M")
        self.assertEqual(payload["lower"], "17.9% lower")
        self.assertEqual(payload["higher"], "7.2% higher")
        self.assertEqual(payload["matched"], "Matched estimate")
        self.assertEqual(
            payload["matchedSentence"],
            "Actual usage matched the pre-run estimate.",
        )
        self.assertTrue(payload["within"])
        self.assertFalse(payload["outside"])
        self.assertEqual(
            payload["incomplete"],
            "Complete usage was reported for 50.0% of calls.",
        )
        self.assertEqual(payload["stage"], "Test set")
        self.assertEqual(
            payload["notes"],
            {
                "caveats": [
                    "Opaque callable target usage is excluded.",
                    "Endpoint usage cannot be estimated.",
                    "Sandbox usage is not included.",
                    "Exclusions: unobservable target calls.",
                ],
                "details": ["A local heuristic estimate."],
            },
        )


if __name__ == "__main__":
    unittest.main()
