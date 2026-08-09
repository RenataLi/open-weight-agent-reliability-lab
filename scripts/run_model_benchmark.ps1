param(
    [ValidateRange(1, 100)]
    [int]$Repetitions = 1,
    [string]$RunId = "",
    [string]$TaskId = ""
)

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
if ([string]::IsNullOrWhiteSpace($RunId)) {
    $RunId = "qwen35-policy-aware-$(Get-Date -Format 'yyyyMMddTHHmmssfff')"
}

$arguments = @(
    "run", "agent-reliability-lab", "agent", "run",
    "benchmarks/smoke_suite.json",
    "--prompts", "benchmarks/model_prompts_v1.json",
    "--base-url", "http://127.0.0.1:8000/v1",
    "--model", "qwen3.5-9b",
    "--model-revision", "cc5442c03a5c0bff0bd4c6888d9a40029c637733",
    "--policy", "policy_aware",
    "--chat-template-mode", "disable_thinking",
    "--repetitions", $Repetitions,
    "--seed", "42",
    "--temperature", "0",
    "--top-p", "1",
    "--max-tokens", "2048",
    "--max-turns", "12",
    "--timeout-seconds", "300",
    "--provider-attempts", "3",
    "--run-id", $RunId
)
if (-not [string]::IsNullOrWhiteSpace($TaskId)) {
    $arguments += @("--task-id", $TaskId)
}

Push-Location $projectRoot
try {
    & $uv @arguments
    $benchmarkExit = $LASTEXITCODE
}
finally {
    Pop-Location
}

if ($benchmarkExit -ne 0) {
    Write-Warning "Benchmark finished with exit code $benchmarkExit."
}
exit $benchmarkExit
