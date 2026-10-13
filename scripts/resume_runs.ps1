# Resume (or start) the Groq benchmark runs. Safe to run any time:
# - finished trials are kept, infra-error trials are retried
# - a run that is already going is not started twice (results/<run>/run.lock)
# - runs pace themselves through Groq's rolling daily token quota
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\resume_runs.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

docker info --format '{{.ServerVersion}}' 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Starting Docker Desktop..."
    Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep 5
        docker info --format '{{.ServerVersion}}' 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { break }
    }
}
docker compose up -d | Out-Null
New-Item -ItemType Directory -Force logs | Out-Null

$tob = Join-Path $root ".venv\Scripts\tob.exe"
$env:PYTHONUNBUFFERED = "1"; $env:PYTHONIOENCODING = "utf-8"
$runs = @(
    @{ model = "groq:openai/gpt-oss-120b"; id = "20261013_groq_gpt-oss-120b"; db = "tenantops"; log = "gpt-oss-120b" },
    @{ model = "groq:qwen/qwen3.8-27b";    id = "20261013_groq_qwen3.8-27b";  db = "tenantops2"; log = "qwen3.8-27b" }
)
foreach ($r in $runs) {
    $env:TOB_DATABASE_URL = "postgresql://tob_owner:tob_owner_pw@localhost:5433/$($r.db)"
    $env:TOB_TENANT_DATABASE_URL = "postgresql://tenant_app:tenant_app_pw@localhost:5433/$($r.db)"
    $cmd = "/c `"$tob`" run --model $($r.model) --tasks all --trials 3 --run-id $($r.id) >> logs\$($r.log).log 2>&1"
    $p = Start-Process -FilePath "cmd.exe" -ArgumentList $cmd -WorkingDirectory $root -WindowStyle Hidden -PassThru
    Write-Host "launched $($r.id) (pid $($p.Id))"
}
Start-Sleep 15
& $tob status
