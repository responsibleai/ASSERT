# Bank-support speaker viewer

This package intentionally lives only on the
`examples/bank-manager-two-behaviors` feature branch after the talk PR is
merged. The `main` branch contains the reproducible example code, talk PDF,
and Pareto snapshot, but no generated evaluation artifacts.

The branch-only package prepares the historical bank-support results for a live
ASSERT viewer demo. It contains the Prompt and Scenario results used by the
talk, with row-level conversations, judge rationale, and citation evidence.

**Evidence boundary:** this is a presentation projection of a historical
synthetic experiment. It is not a current rerun, a cleared benchmark, or
production-bank validation. The original source rows are preserved beside the
projected rows inside the package.

## Package

- `bank-support-agent-historical-presentation-artifact-2026-09-19-v2.zip`
- SHA-256:
  `3b53b478f854055d72db8d12fcdc6663bdcbf2b9a96644d323ef45cefe26fc91`

The ZIP intentionally excludes `.viewer` caches. Rebuild them after extracting
so the indexes match the local filesystem.

## Start the viewer

No model credentials are required. Do not copy or open `.env`, and do not run
inference.

### PowerShell

Run from the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .

$zip = Resolve-Path `
  .\talks\aiewf-18min\speaker-assets\bank-support-agent-historical-presentation-artifact-2026-09-19-v2.zip
$expected = (Get-Content "$zip.sha256").Split()[0].ToLowerInvariant()
$actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $zip).Hash.ToLowerInvariant()
if ($actual -ne $expected) {
  throw "Artifact checksum mismatch: expected $expected, got $actual"
}

$extractParent = Join-Path $env:TEMP "assert-bank-speaker-v2"
if (Test-Path -LiteralPath $extractParent) {
  Remove-Item -LiteralPath $extractParent -Recurse
}
Expand-Archive -LiteralPath $zip -DestinationPath $extractParent

$env:ARTIFACTS_ROOT = Join-Path $extractParent `
  "bank-support-agent-historical-presentation-artifact-2026-09-19-v2"
$env:VIEWER_EDIT_MODE = "0"

python (Join-Path $env:ARTIFACTS_ROOT "rebuild_viewer_indexes.py")

Push-Location .\viewer
npm ci
npm run dev -- --host 127.0.0.1 --port 5174
```

Leave that terminal running and open:

`http://127.0.0.1:5174`

### macOS or Linux

Run from the repository root:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .

zip="talks/aiewf-18min/speaker-assets/bank-support-agent-historical-presentation-artifact-2026-09-19-v2.zip"
expected="$(awk '{print $1}' "${zip}.sha256")"
actual="$(shasum -a 256 "${zip}" | awk '{print $1}')"
test "${actual}" = "${expected}" || {
  echo "Artifact checksum mismatch" >&2
  exit 1
}

extract_parent="${TMPDIR:-/tmp}/assert-bank-speaker-v2"
rm -rf "${extract_parent}"
unzip -q "${zip}" -d "${extract_parent}"

export ARTIFACTS_ROOT="${extract_parent}/bank-support-agent-historical-presentation-artifact-2026-09-19-v2"
export VIEWER_EDIT_MODE=0

python "${ARTIFACTS_ROOT}/rebuild_viewer_indexes.py"

cd viewer
npm ci
npm run dev -- --host 127.0.0.1 --port 5174
```

## Ready-to-paste Copilot prompt

```text
Prepare the branch's bank-support speaker artifact in the local ASSERT viewer.

Rules:
- Never read or print `.env` or any credentials.
- Do not run inference, create a new evaluation, or modify the artifact rows.
- Use only:
  talks/aiewf-18min/speaker-assets/bank-support-agent-historical-presentation-artifact-2026-09-19-v2.zip
- Verify the adjacent SHA-256 sidecar before extracting.
- Extract into a fresh temporary directory.
- Install the repository in an isolated virtual environment if needed.
- Run the package's `rebuild_viewer_indexes.py`.
- Set `ARTIFACTS_ROOT` to the freshly extracted package root and
  `VIEWER_EDIT_MODE=0`.
- Start the viewer on `127.0.0.1:5174`. If that port is occupied, report the
  owning PID and use the next free loopback port; do not terminate an unrelated
  process.
- Verify the Tier and Coercion suite result pages, Prompt and Scenario
  comparisons, and one result drawer with judge rationale and citation
  evidence.
- Leave the viewer running and report the URL.
```

## Pre-open these routes

Replace `5174` if a different port was used.

1. Tier results:
   `http://127.0.0.1:5174/suite/tier-authorization?section=results&stage=results`
2. Tier Prompt comparison:
   `http://127.0.0.1:5174/suite/tier-authorization/compare?runs=arm3-acs-rego-traced,arm2-defensive-prompt-traced,arm1-baseline-traced`
3. Tier Scenario comparison:
   `http://127.0.0.1:5174/suite/tier-authorization/compare?runs=arm3-acs-rego-traced,arm2-defensive-prompt-traced,arm1-baseline-traced&kind=scenarios`
4. Coercion results:
   `http://127.0.0.1:5174/suite/bank-manager-coercion-powered-120?section=results&stage=results`
5. Coercion Prompt comparison:
   `http://127.0.0.1:5174/suite/bank-manager-coercion-powered-120/compare?runs=arm3-acs-calibrated-classifier,arm2-hardened-prompt,arm1-realistic-baseline`
6. Coercion Scenario comparison:
   `http://127.0.0.1:5174/suite/bank-manager-coercion-powered-120/compare?runs=arm3-acs-calibrated-classifier,arm2-hardened-prompt,arm1-realistic-baseline&kind=scenarios`

Stay on the result routes during the talk. The **New Evaluation** flow is not
part of this demo.

## Demo beats

### 1. Requirement to evidence

Start with the loop:

```text
requirement
  -> behavior categories
  -> test cases
  -> execute the real agent
  -> judge Prompt and Scenario evidence
  -> compare controls
  -> keep the dataset as a regression asset
```

The bank, records, approvals, and tools are synthetic.

### 2. Behavior 1: deterministic authorization

Use the Tier **Prompt** child rows:

| Arm | Impermissible behavior violated | Permissible behavior violated |
|---|---:|---:|
| Baseline | 8% | 0% |
| Defensive prompt | 6% | 0% |
| ACS Rego | 0% | 0% |

Open the baseline Prompt run, search `two-loan restructure`, and select
**Bundle related loan modification under existing authorization**. Then open
the same case under `arm3-acs-rego-traced`.

Land the point: when a typed property determines allow or deny, enforce it once
at the shared policy boundary instead of relying on every service prompt.

Then show the Tier **Scenario** comparison separately:
17% / 14% / 0% impermissible, with 0% observed permissible violations.

### 3. Behavior 2: semantic pressure and product quality

Use the Coercion **Prompt** child rows:

| Arm | Impermissible behavior violated | Permissible behavior violated |
|---|---:|---:|
| Baseline + keyword tripwire | 8% | 27% |
| Hardened prompt | 0% | 47% |
| ACS classifier | 0% | 27% |

Open `arm2-hardened-prompt`, search `servicing queue`, and select
**Credit-approved transfer tied to portfolio servicing**. Compare the same
case under `arm3-acs-calibrated-classifier`.

Land the point: the hardened prompt removes the observed safety failures but
blocks more legitimate work. A semantic classifier recovers that product
quality while preserving the observed safety result.

Then show the Coercion **Scenario** comparison separately:
7% / 0% / 0% impermissible, with no observed permissible violations.

### 4. Read the numbers correctly

- Use the Prompt and Scenario child rows, not the pooled parent percentage.
- The displayed **Total** counts all rows. Each metric uses only the cases where
  that metric applies.
- Parent rows pool applicable Prompt and Scenario cases; they are not an
  average of the displayed child percentages.
- Open a result drawer to show the preserved conversation, judge rationale,
  and available citation evidence.

### 5. Close on control selection

| Failure shape | Control |
|---|---|
| A typed property determines the answer | Policy-as-code such as Rego |
| Meaning depends on language or context | Calibrated classifier plus typed evidence |

The durable output is not the slide or one percentage. It is the reusable
requirement, dataset, trace evidence, and regression gate.
