$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$localUv = Join-Path $projectRoot ".tools\uv-bootstrap\Scripts\uv.exe"
$uvCommand = Get-Command uv -ErrorAction SilentlyContinue

if ($null -ne $uvCommand) {
    $uv = $uvCommand.Source
} elseif (Test-Path -LiteralPath $localUv) {
    $uv = $localUv
} else {
    throw "uv was not found. Install uv or create the project-local bootstrap environment."
}

Push-Location $projectRoot
try {
    & $uv run ruff format --check src tests
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & $uv run ruff check src tests
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & $uv run mypy
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & $uv run pytest
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} finally {
    Pop-Location
}
