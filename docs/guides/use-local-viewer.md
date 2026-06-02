# View results in Local UI Viewer Application

Though portable local artifacts can be a powerful feature, ASSERT comes with a local-hosted UI viewer web application that helps richly render the results and artifacts to help with analysis.

- suite list with taxonomy and test-case counts
- taxonomy browser
- prompt browser (single-turn cases)
- scenario browser (multi-turn transcripts)
- run comparison views
- dimension breakdowns
- inference preview while runs are in progress
- live run monitor from `manifest.json`

## Screenshot walkthrough

### Suite list

Use the suite list page to quickly scan available suites and jump to a specific run.

![Viewer suite list](../../assets/viewer-suites.png)

### Suite overview tabs

The suite details page contains tabs for taxonomy, test set content, and run-level evaluation results.

![Suite taxonomy tab](../../assets/viewer-suites-taxonomy.png)

![Suite test set tab](../../assets/viewer-suites-testset.png)

![Suite evaluation results overview](../../assets/viewer-suites-eval-results.png)

### Run summary and result tables

Within a run, the viewer exposes a high-level summary plus row-level result drilldowns.

![Run summary](../../assets/viewer-suites-eval-results-run-summary.png)

![Run verdict table](../../assets/viewer-suites-eval-results-run-result-verdict.png)

![Run rows view](../../assets/viewer-suites-eval-results-run-rows.png)

### Behavior breakdown view

Use this view to inspect outcomes grouped by behavior category.

![Suites behavior breakdown](../../assets/viewer-suites-behavior.png)

### Create a new run flow

The "Create new run" flow guides you through selecting source artifacts and run settings.

![Create new run step 1](../../assets/create-new-run-1.png)

![Create new run step 2](../../assets/create-new-run-2.png)

![Create new run step 3](../../assets/create-new-run-3.png)

### Compare runs

Use the compare view to inspect differences across runs side by side.

![Compare runs view](../../assets/compare-runs.png)
