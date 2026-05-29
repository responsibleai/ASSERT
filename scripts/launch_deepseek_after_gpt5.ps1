# Watcher: wait for the in-flight gpt-5 sweep to finish its final combo
# (variant-d-guarded-gepa-gpt-5\metrics.json), then launch the 4-variant
# DeepSeek-V4-Flash sweep (n=100). Idempotent: skips combos whose metrics.json
# already exists.

$ErrorActionPreference = 'Continue'

$watchFile = Join-Path $PSScriptRoot '..\artifacts\results\bank-manager-agent-shield\variant-d-guarded-gepa-gpt-5\metrics.json'
$maxWaitSec = 6 * 60 * 60   # 6h safety cap
$pollSec = 60
$settleSec = 60             # let the gpt-5 sweep terminate cleanly after metrics.json appears

$start = Get-Date
Write-Host "[watcher] Waiting for: $watchFile"

while (-not (Test-Path $watchFile)) {
    if (((Get-Date) - $start).TotalSeconds -gt $maxWaitSec) {
        Write-Host "[watcher] Timed out after $maxWaitSec sec waiting for gpt-5 sweep. Aborting." -ForegroundColor Red
        exit 1
    }
    Start-Sleep -Seconds $pollSec
}

Write-Host "[watcher] gpt-5 sweep complete. Settling $settleSec sec..." -ForegroundColor Green
Start-Sleep -Seconds $settleSec

# ── Launch DeepSeek-V4-Flash sweep ────────────────────────────────────────
$env:AGENT_MODEL = 'DeepSeek-V4-Flash'
$slug = 'deepseek-v4-flash'
$variants = @(
    @{ tag = 'a-unguarded';    cfg = 'eval_config_unguarded.yaml' },
    @{ tag = 'b-guarded';      cfg = 'eval_config_guarded.yaml' },
    @{ tag = 'c-naive-prompt'; cfg = 'eval_config_naive_prompt.yaml' },
    @{ tag = 'd-guarded-gepa'; cfg = 'eval_config_guarded_gepa.yaml' }
)

Push-Location (Join-Path $PSScriptRoot '..')
try {
    foreach ($v in $variants) {
        $run = "variant-$($v.tag)-$slug"
        $metricsPath = "artifacts\results\bank-manager-agent-shield\$run\metrics.json"
        if (Test-Path $metricsPath) {
            Write-Host "`n>>> [DeepSeek-V4-Flash] $($v.cfg) | run=$run  (SKIP, metrics.json exists)" -ForegroundColor Yellow
            continue
        }
        Write-Host "`n>>> [DeepSeek-V4-Flash] $($v.cfg) | run=$run" -ForegroundColor Cyan
        p2m run --config "examples\bank_manager_agent_shield\$($v.cfg)" `
            --override "test_set.sample_size=100" `
            --override "run=$run"
        if ($LASTEXITCODE -ne 0) {
            Write-Host "FAILED: $run (continuing to next)" -ForegroundColor Red
        }
    }
}
finally {
    Pop-Location
}

Write-Host "`n=== DeepSeek-V4-Flash SWEEP COMPLETE ===" -ForegroundColor Green
