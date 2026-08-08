$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
docker compose --project-directory $projectRoot -f "$projectRoot\deploy\compose.gpu.yml" down
if ($LASTEXITCODE -ne 0) {
    throw "Docker Compose failed to stop the model service."
}
