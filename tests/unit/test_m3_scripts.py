from __future__ import annotations

from pathlib import Path

from agent_reliability_lab.study import canonical_sha256, load_study_spec

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_cell_runner_validates_current_corrective_protocol_before_agent_run() -> None:
    spec = load_study_spec(PROJECT_ROOT / "studies" / "m3_protocol.json")
    script = (PROJECT_ROOT / "scripts" / "m3_run_cell.ps1").read_text(encoding="utf-8")
    protocol_check = script.index("study plan studies/m3_protocol.json --json")
    agent_run = script.index("& $uv @arguments")

    assert protocol_check < agent_run
    assert "$resolvedPlan.study_id -ne $StudyId" in script
    assert "$resolvedPlan.protocol_sha256 -ne $ProtocolSha256" in script
    assert spec.study_id == "m3-controlled-contract-study-v1r2"
    assert len(canonical_sha256(spec)) == 64


def test_matrix_and_cell_runners_use_only_the_corrective_output_root() -> None:
    cell_script = (PROJECT_ROOT / "scripts" / "m3_run_cell.ps1").read_text(encoding="utf-8")
    matrix_script = (PROJECT_ROOT / "scripts" / "m3_run_matrix.ps1").read_text(encoding="utf-8")

    assert '$OutputDir = "runs\\$StudyId\\cells"' in cell_script
    assert '$studyId = "m3-controlled-contract-study-v1r2"' in matrix_script
    assert "m3-controlled-contract-study-v1r1" not in cell_script
    assert "m3-controlled-contract-study-v1r1" not in matrix_script
    assert '"runs\\m3-controlled-contract-study-v1"' not in cell_script
    assert '"runs\\m3-controlled-contract-study-v1"' not in matrix_script
