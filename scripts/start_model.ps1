$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$cachePath = Join-Path $projectRoot "models\huggingface"
New-Item -ItemType Directory -Path $cachePath -Force | Out-Null

docker compose --project-directory $projectRoot -f "$projectRoot\deploy\compose.gpu.yml" up -d
if ($LASTEXITCODE -ne 0) {
    throw "Docker Compose failed to start the model service."
}
docker compose --project-directory $projectRoot -f "$projectRoot\deploy\compose.gpu.yml" ps
if ($LASTEXITCODE -ne 0) {
    throw "Docker Compose could not inspect the model service."
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
        throw "The model container stopped before becoming healthy."
    }
    if ($attempt % 12 -eq 0) {
        Write-Output "Waiting for model health ($attempt/360). First startup downloads weights."
    }
    Start-Sleep -Seconds 5
}

if (-not $healthy) {
    docker logs --tail 200 agent-reliability-vllm
    throw "The model server did not become healthy within 30 minutes."
}

Write-Output "Qwen3.5-9B is healthy at http://127.0.0.1:8000/v1"
