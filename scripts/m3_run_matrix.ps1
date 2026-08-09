param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("qwen", "ministral")]
    [string]$ModelProfile
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

$studyId = "m3-controlled-contract-study-v1r2"
$studyRoot = Join-Path $projectRoot "runs\$studyId"
$cellsRoot = Join-Path $studyRoot "cells"
$planPath = Join-Path $studyRoot "study-plan.json"
$modelKey = if ($ModelProfile -eq "qwen") { "qwen35-9b" } else { "ministral3-8b" }

function Assert-CellCommit {
    param(
        [Parameter(Mandatory = $true)][string]$CellDirectory,
        [Parameter(Mandatory = $true)][string]$ExpectedCommit
    )
    $manifestPath = Join-Path $CellDirectory "manifest.json"
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    if ($manifest.git_commit -ne $ExpectedCommit) {
        throw "Cell $($manifest.run_id) belongs to Git commit $($manifest.git_commit), not current frozen commit $ExpectedCommit."
    }
}

Push-Location $projectRoot
try {
    $gitStatus = git status --porcelain --untracked-files=normal
    if ($LASTEXITCODE -ne 0) {
        throw "The fixed study protocol requires a readable local Git revision."
    }
    if ($gitStatus) {
        throw "The fixed study protocol must run from a clean Git worktree."
    }
    $studyCommit = (git rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0 -or $studyCommit -notmatch "^[0-9a-f]{40}$") {
        throw "The fixed study protocol requires one immutable Git commit."
    }

    & $uv run agent-reliability-lab study plan studies/m3_protocol.json --output $planPath
    if ($LASTEXITCODE -ne 0) {
        throw "The frozen M3 protocol or one of its pinned inputs failed validation."
    }
    $plan = Get-Content -LiteralPath $planPath -Raw | ConvertFrom-Json
    if ($plan.study_id -ne $studyId) {
        throw "The expanded plan belongs to $($plan.study_id), not corrective study $studyId."
    }
    $selectedCells = @($plan.cells | Where-Object { $_.model_key -eq $modelKey })
    if ($selectedCells.Count -ne 12) {
        throw "Expected 12 protocol-defined cells for $modelKey, observed $($selectedCells.Count)."
    }

    try {
        & (Join-Path $PSScriptRoot "m3_start_model.ps1") -ModelProfile $ModelProfile
        foreach ($cell in $selectedCells) {
            $cellDirectory = Join-Path $cellsRoot $cell.cell_id
            $requiredArtifacts = @("manifest.json", "events.jsonl", "result.json")
            $existingArtifacts = @(
                $requiredArtifacts | Where-Object {
                    Test-Path -LiteralPath (Join-Path $cellDirectory $_) -PathType Leaf
                }
            )
            if (Test-Path -LiteralPath $cellDirectory) {
                if ($existingArtifacts.Count -eq $requiredArtifacts.Count) {
                    Assert-CellCommit -CellDirectory $cellDirectory -ExpectedCommit $studyCommit
                    & $uv run agent-reliability-lab study validate-cell $cell.cell_id `
                        --protocol studies/m3_protocol.json --cells-dir $cellsRoot
                    if ($LASTEXITCODE -ne 0) {
                        throw "Existing cell $($cell.cell_id) failed immutable artifact validation."
                    }
                    Write-Output "Keeping validated immutable cell $($cell.cell_id)."
                    continue
                }
                throw "Cell $($cell.cell_id) exists but is incomplete; the frozen study forbids overwrite or rerun."
            }

            & (Join-Path $PSScriptRoot "m3_run_cell.ps1") `
                -ModelProfile $ModelProfile `
                -ContractVersion $cell.contract_version `
                -ChatTemplateMode $cell.chat_template_mode `
                -StudyId $plan.study_id `
                -ProtocolSha256 $plan.protocol_sha256 `
                -Seed $cell.seed `
                -Temperature $cell.temperature `
                -TopP $cell.top_p
            $cellExit = $LASTEXITCODE
            $complete = $true
            foreach ($artifactName in $requiredArtifacts) {
                if (-not (Test-Path -LiteralPath (Join-Path $cellDirectory $artifactName) -PathType Leaf)) {
                    $complete = $false
                }
            }
            if ($cellExit -notin 0, 1 -or -not $complete) {
                throw "Cell $($cell.cell_id) ended with infrastructure/configuration failure code $cellExit."
            }
            Assert-CellCommit -CellDirectory $cellDirectory -ExpectedCommit $studyCommit
            & $uv run agent-reliability-lab study validate-cell $cell.cell_id `
                --protocol studies/m3_protocol.json --cells-dir $cellsRoot
            if ($LASTEXITCODE -ne 0) {
                throw "New cell $($cell.cell_id) failed immutable artifact validation."
            }
            Write-Output "Recorded scored cell $($cell.cell_id) (benchmark exit $cellExit)."
        }
    }
    finally {
        & (Join-Path $PSScriptRoot "m3_stop_model.ps1") -ModelProfile $ModelProfile
    }

    $completeCellCount = @(
        $plan.cells | Where-Object {
            $candidate = Join-Path $cellsRoot $_.cell_id
            (Test-Path -LiteralPath (Join-Path $candidate "manifest.json") -PathType Leaf) -and
            (Test-Path -LiteralPath (Join-Path $candidate "events.jsonl") -PathType Leaf) -and
            (Test-Path -LiteralPath (Join-Path $candidate "result.json") -PathType Leaf)
        }
    ).Count
    if ($completeCellCount -eq $plan.cells.Count) {
        & $uv run agent-reliability-lab study report studies/m3_protocol.json `
            --cells-dir $cellsRoot --output-dir $studyRoot
        exit $LASTEXITCODE
    }
    Write-Output "Completed $completeCellCount/$($plan.cells.Count) cells. Run the other model profile next."
}
finally {
    Pop-Location
}
