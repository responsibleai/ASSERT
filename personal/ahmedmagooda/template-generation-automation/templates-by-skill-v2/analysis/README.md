# Harm-template analysis artifacts

Azure LLM-and-embeddings grading outputs for `templates-by-skill-v2`.

- `report.md`: methodology and result tables.
- `metrics.json`: complete metrics, model/endpoints, prompts, and judgments.
- `dimension_inventory.csv`: one row per test-set dimension, including relevance
    and expected adversarial-pressure judgments.
- `pairwise_similarity.csv`: embedding and LLM scores for every same-harm pair.
- `unique_dimension_groups.csv`: global per-harm deduplication groups and representatives.
- `coverage_weak_points.csv`: prioritized coverage gaps and flattened dimension proposals.
- `coverage_dimension_suggestions.yaml`: gap context and ready-to-adapt dimensions with levels.
- `run-prefixes/<harm>/runs-1-to-<N>/`: complete metrics, CSVs, and report for
    each selected cumulative run prefix.
- `api_cache.json`: request-keyed Azure responses; may contain grader outputs but no credentials.
- `../../evaluate_template_generation.py`: executable analysis and all prompts.

Run from the repository root:

```bash
python personal/ahmedmagooda/template-generation-automation/evaluate_template_generation.py templates-by-skill-v2 \
    --endpoint <azure-openai-resource-endpoint> \
    --embedding-deployment <azure-embedding-deployment> \
  --judge-model <azure-responses-deployment>
```

Set `AZURE_API_KEY` (or `AZURE_OPENAI_API_KEY`) for key auth. If neither is
present, the script uses ASSERT's Entra ID token provider and requires the
`azure-aad` extra. `--judge-model` can instead be supplied through
`ASSERT_ANALYSIS_JUDGE_MODEL`; the endpoint and embedding deployment can be set
through `AZURE_API_BASE` and `ASSERT_ANALYSIS_EMBEDDING_DEPLOYMENT`. Credentials
are never written to artifacts.

Use `--validate-only` to parse inputs and verify expected pair counts without
network access. Root analysis artifacts describe each harm's final `1..N`
prefix, and root `metrics.json` includes `cumulative_run_results` linking every
prefix. Use `--no-cache` for a fully fresh grader run.
