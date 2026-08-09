param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("qwen", "ministral")]
    [string]$ModelProfile,
    [Parameter(Mandatory = $true)]
    [ValidateSet("v1", "v2")]
    [string]$ContractVersion,
    [Parameter(Mandatory = $true)]
    [ValidateSet("disable_thinking", "native")]
    [string]$ChatTemplateMode,
    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[a-z0-9]+(?:-[a-z0-9]+)*$")]
    [string]$StudyId,
    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[0-9a-f]{64}$")]
    [string]$ProtocolSha256,
    [Parameter(Mandatory = $true)]
    [ValidateRange(0, 2147483647)]
    [int]$Seed,
    [ValidateRange(0.0, 2.0)]
    [double]$Temperature = 0.3,
    [ValidateRange(0.001, 1.0)]
    [double]$TopP = 1.0
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
        ChatTemplateMode = "disable_thinking"
    }
    ministral = @{
        Model = "ministral3-8b"
        Revision = "f6fae9795746f63c9be8344932f01275f3c63734"
        Compose = "deploy/compose.ministral3-8b.yml"
        ChatTemplateMode = "native"
    }
}
$profile = $profiles[$ModelProfile]
if ($StudyId -ne "m3-controlled-contract-study-v1r2") {
    throw "This runner writes only the corrective M3 study root, not $StudyId."
}
if ($ChatTemplateMode -ne $profile.ChatTemplateMode) {
    throw "Chat-template mode $ChatTemplateMode does not match the pinned $ModelProfile stack ($($profile.ChatTemplateMode))."
}
if ($TopP -ne 1.0) {
    throw "The fixed M3 protocol requires top_p=1.0."
}
if ($Temperature -eq 0.3 -and $Seed -in 11, 23, 42, 71, 97) {
    $phasePrefix = "m3s"
}
elseif ($Temperature -eq 0.0 -and $Seed -eq 0) {
    $phasePrefix = "m3g"
}
else {
    throw "The requested seed/temperature pair is outside the fixed M3 matrix."
}
$modelKey = if ($ModelProfile -eq "qwen") { "qwen35-9b" } else { "ministral3-8b" }
$RunId = "$phasePrefix-$modelKey-$ContractVersion-s$($Seed.ToString('00000000'))-r01"
$OutputDir = "runs\$StudyId\cells"

$arguments = @(
    "run", "agent-reliability-lab", "agent", "run",
    "benchmarks/m3_holdout_suite.json",
    "--prompts", "benchmarks/m3_holdout_prompts.json",
    "--base-url", "http://127.0.0.1:8000/v1",
    "--model", $profile.Model,
    "--model-revision", $profile.Revision,
    "--deployment-compose", $profile.Compose,
    "--policy", "policy_aware",
    "--contract-version", $ContractVersion,
    "--study-id", $StudyId,
    "--study-protocol-sha256", $ProtocolSha256,
    "--chat-template-mode", $ChatTemplateMode,
    "--repetitions", "1",
    "--seed", $Seed,
    "--temperature", $Temperature.ToString([Globalization.CultureInfo]::InvariantCulture),
    "--top-p", $TopP.ToString([Globalization.CultureInfo]::InvariantCulture),
    "--max-tokens", "2048",
    "--max-turns", "12",
    "--timeout-seconds", "300",
    "--provider-attempts", "3",
    "--output-dir", $OutputDir,
    "--run-id", $RunId
)
Push-Location $projectRoot
try {
    $resolvedPlanJson = & $uv run agent-reliability-lab study plan studies/m3_protocol.json --json
    if ($LASTEXITCODE -ne 0) {
        throw "The corrective-study protocol or one of its hash-bound inputs failed validation."
    }
    $resolvedPlan = ($resolvedPlanJson -join "`n") | ConvertFrom-Json
    if ($resolvedPlan.study_id -ne $StudyId -or $resolvedPlan.protocol_sha256 -ne $ProtocolSha256) {
        throw "Study identity or protocol SHA-256 differs from the current canonical protocol."
    }
    & $uv @arguments
    $benchmarkExit = $LASTEXITCODE
}
finally {
    Pop-Location
}

if ($benchmarkExit -ne 0) {
    Write-Warning "M3 cell finished with exit code $benchmarkExit."
}
exit $benchmarkExit
