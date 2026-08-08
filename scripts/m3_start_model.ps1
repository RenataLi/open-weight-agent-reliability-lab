param(
    [ValidateSet("qwen", "ministral")]
    [string]$ModelProfile = "qwen"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$profiles = @{
    qwen = @{
        Compose = "deploy\compose.gpu.yml"
        DisplayName = "Qwen3.5-9B"
        ServedModel = "qwen3.5-9b"
    }
    ministral = @{
        Compose = "deploy\compose.ministral3-8b.yml"
        DisplayName = "Ministral-3-8B-Instruct-2512-BF16"
        ServedModel = "ministral3-8b"
    }
}
$profile = $profiles[$ModelProfile]
$composePath = Join-Path $projectRoot $profile.Compose
$cachePath = Join-Path $projectRoot "models\huggingface"

if (-not (Test-Path -LiteralPath $composePath -PathType Leaf)) {
    throw "Deployment profile is missing: $composePath"
}
New-Item -ItemType Directory -Path $cachePath -Force | Out-Null

docker compose --project-directory $projectRoot -f $composePath config --quiet
if ($LASTEXITCODE -ne 0) {
    throw "Docker Compose rejected the $ModelProfile deployment profile."
}

docker compose --project-directory $projectRoot -f $composePath up -d --remove-orphans
if ($LASTEXITCODE -ne 0) {
    throw "Docker Compose failed to start the $ModelProfile model service."
}

$healthy = $false
for ($attempt = 1; $attempt -le 360; $attempt++) {
    $status = docker inspect --format "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}" agent-reliability-vllm 2>$null
    if ($LASTEXITCODE -eq 0 -and $status -eq "healthy") {
        $healthy = $true
        break
    }
    if ($LASTEXITCODE -eq 0 -and $status -in "exited", "dead", "restarting") {
        docker logs --tail 200 agent-reliability-vllm
        throw "The $ModelProfile model container stopped before becoming healthy."
    }
    if ($attempt % 12 -eq 0) {
        Write-Output "Waiting for $($profile.DisplayName) health ($attempt/360). First startup may download weights."
    }
    Start-Sleep -Seconds 5
}

if (-not $healthy) {
    docker logs --tail 200 agent-reliability-vllm
    throw "The $ModelProfile model server did not become healthy within 30 minutes."
}

try {
    $modelResponse = Invoke-RestMethod -Uri "http://127.0.0.1:8000/v1/models" -TimeoutSec 15
    $servedIds = @($modelResponse.data | ForEach-Object { $_.id })
}
catch {
    throw "The model container is healthy, but its model identity endpoint is unavailable: $($_.Exception.Message)"
}
if ($profile.ServedModel -notin $servedIds) {
    throw "Expected served model '$($profile.ServedModel)', observed: $($servedIds -join ', ')."
}

docker compose --project-directory $projectRoot -f $composePath ps
if ($LASTEXITCODE -ne 0) {
    throw "Docker Compose could not inspect the $ModelProfile model service."
}
Write-Output "$($profile.DisplayName) is healthy at http://127.0.0.1:8000/v1"
