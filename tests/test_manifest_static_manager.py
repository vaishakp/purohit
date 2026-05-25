from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import yaml

from reanalyze import static_manager


def test_submit_event_routes_manifest_to_condor_submit(tmp_path, monkeypatch):
    project = tmp_path / "project"
    event_dir = project / "working" / "ce_event_000000__baseline_220"
    event_dir.mkdir(parents=True)
    submit_file = event_dir / "job.submit"
    submit_file.write_text("queue 1\n")
    config = event_dir / "ce_event_000000_baseline_220.ini"
    config.write_text("[input]\n")
    status_path = event_dir / "status.yaml"
    status_path.write_text(
        yaml.safe_dump(
            {
                "status": "pending",
                "workflow_type": "manifest",
                "application": "pyring",
                "submit_file": str(submit_file),
                "submit_ini": str(config),
                "output": str(event_dir / "out"),
            }
        )
    )

    commands = []

    def fake_run_checked(command):
        commands.append(command)
        return SimpleNamespace(stdout="1 job(s) submitted to cluster 12345.\n", stderr="")

    monkeypatch.setattr(static_manager, "run_checked", fake_run_checked)

    result = static_manager.submit_event(project, event_dir.name)

    assert result["ok"] is True
    assert result["jobid"] == "12345"
    assert commands == [["condor_submit", str(submit_file)]]
    assert (project / "submitted_jobs.txt").read_text().strip() == event_dir.name
    status = yaml.safe_load(status_path.read_text())
    assert status["workflow_type"] == "manifest"
    assert status["application"] == "pyring"
    assert status["status"] == "submitted"
    assert status["jobid"] == "12345"


def test_submit_event_keeps_bilby_path_for_non_manifest(tmp_path, monkeypatch):
    project = tmp_path / "project"
    event_dir = project / "working" / "S1"
    event_dir.mkdir(parents=True)
    config = event_dir / "config.ini"
    config.write_text("label = S1\n")

    commands = []

    def fake_stage_bilby_inputs(project_dir, event, config_path):
        return SimpleNamespace(
            enabled=False,
            config_path=config_path,
            manifest_path=None,
            copied_files=(),
        )

    def fake_run_checked(command):
        commands.append(command)
        return SimpleNamespace(stdout="submitted to cluster 98765\n", stderr="")

    monkeypatch.setattr(static_manager, "stage_bilby_inputs", fake_stage_bilby_inputs)
    monkeypatch.setattr(static_manager, "run_checked", fake_run_checked)

    result = static_manager.submit_event(project, "S1")

    assert result["ok"] is True
    assert result["jobid"] == "98765"
    assert commands == [["bilby_pipe", str(config), "--submit"]]
    status = yaml.safe_load((event_dir / "status.yaml").read_text())
    assert status["workflow_type"] == "bilby_pipe"
    assert status["application"] == "bilby"
