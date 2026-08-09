from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
START_MODEL_SCRIPT = PROJECT_ROOT / "scripts" / "m3_start_model.ps1"


def test_model_start_checks_container_state_separately_from_health() -> None:
    script = START_MODEL_SCRIPT.read_text(encoding="utf-8-sig")

    inspect_format = (
        "{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}"
    )
    assert inspect_format in script
    assert 'if ($stateStatus -in "exited", "dead", "restarting")' in script
    assert 'if ($stateStatus -eq "running" -and $healthStatus -eq "healthy")' in script

    fail_fast_check = script.index('if ($stateStatus -in "exited", "dead", "restarting")')
    healthy_check = script.index('if ($stateStatus -eq "running" -and $healthStatus -eq "healthy")')
    sleep = script.index("Start-Sleep -Seconds 5")
    assert fail_fast_check < healthy_check < sleep
