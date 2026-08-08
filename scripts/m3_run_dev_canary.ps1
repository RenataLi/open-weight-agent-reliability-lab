param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("qwen", "ministral")]
    [string]$ModelProfile,
    [Parameter(Mandatory = $true)]
    [ValidateSet("v1", "v2")]
    [string]$ContractVersion,
    [Parameter(Mandatory = $true)]
    [ValidateSet("S06_APPROVED_ROLLBACK", "S12_LOST_RESPONSE_AFTER_COMMIT")]
    [string]$TaskId
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$localUv = Join-Path $projectRoot ".tools\uv-bootstrap\Scripts\uv.exe"
$uvCommand = Get-Command uv -ErrorAction SilentlyContinue
if ($null -ne $uvCommand) {
    $uv = $uvCommand.Source
}
elseif (Test-Path -LiteralPath $localUv -PathType Leaf) {
    $uv = $localUv
}
else {
    throw "uv was not found. Install uv or create the project-local bootstrap environment."
}

$profiles = @{
    qwen = @{
        Model = "qwen3.5-9b"
        Revision = "cc5442c03a5c0bff0bd4c6888d9a40029c637733"
        Compose = "deploy/compose.gpu.yml"
    }
    ministral = @{
        Model = "ministral3-8b"
        Revision = "f6fae9795746f63c9be8344932f01275f3c63734"
        Compose = "deploy/compose.ministral3-8b.yml"
    }
}
$profile = $profiles[$ModelProfile]
$runId = "m3-dev-$ModelProfile-$ContractVersion-$($TaskId.ToLowerInvariant())-$(Get-Date -Format 'yyyyMMddTHHmmssfff')"

$arguments = @(
    "run", "agent-reliability-lab", "agent", "run",
    "benchmarks/smoke_suite.json",
    "--prompts", "benchmarks/model_prompts_v1.json",
    "--task-id", $TaskId,
    "--base-url", "http://127.0.0.1:8000/v1",
    "--model", $profile.Model,
    "--model-revision", $profile.Revision,
    "--deployment-compose", $profile.Compose,
    "--policy", "policy_aware",
    "--contract-version", $ContractVersion,
    "--repetitions", "1",
    "--seed", "42",
    "--temperature", "0",
    "--top-p", "1",
    "--max-tokens", "2048",
    "--max-turns", "12",
    "--timeout-seconds", "300",
    "--provider-attempts", "3",
    "--output-dir", "runs/m3-dev-canaries",
    "--run-id", $runId
)

Push-Location $projectRoot
try {
    & $uv @arguments
    $benchmarkExit = $LASTEXITCODE
}
finally {
    Pop-Location
}

exit $benchmarkExit
