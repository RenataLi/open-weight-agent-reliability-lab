param(
    [ValidateSet("qwen", "ministral")]
    [string]$ModelProfile = "qwen"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$composeRelativePath = switch ($ModelProfile) {
    "qwen" { "deploy\compose.gpu.yml" }
    "ministral" { "deploy\compose.ministral3-8b.yml" }
}
$composePath = Join-Path $projectRoot $composeRelativePath

if (-not (Test-Path -LiteralPath $composePath -PathType Leaf)) {
    throw "Deployment profile is missing: $composePath"
}
docker compose --project-directory $projectRoot -f $composePath down --remove-orphans
if ($LASTEXITCODE -ne 0) {
    throw "Docker Compose failed to stop the $ModelProfile model service."
}
