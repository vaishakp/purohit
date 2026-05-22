from __future__ import annotations

from pathlib import Path

import yaml

from reanalyze.static_manager import process_command, reset_event, submitted_jobs


def test_reset_event_removes_submitted_ledger_entry_and_pe_dir(tmp_path):
    project = tmp_path / "project"
    event = "S240413p"
    event_dir = project / "working" / event
    pe_dir = event_dir / "pe"
    pe_dir.mkdir(parents=True)
    (pe_dir / "stale.txt").write_text("old\n")
    (event_dir / "status.yaml").write_text(yaml.safe_dump({"jobid": "12345", "status": "submitted"}))
    (project / "submitted_jobs.txt").write_text(f"{event}\nS240414a\n")

    result = reset_event(project, event)

    assert result["ok"] is True
    assert result["removed_from_submitted_jobs"] is True
    assert result["previous_jobid"] == "12345"
    assert not pe_dir.exists()
    assert submitted_jobs(project) == ["S240414a"]
    status = yaml.safe_load((event_dir / "status.yaml").read_text())
    assert status["status"] == "pending"
    assert status["previous_jobid"] == "12345"
    assert "jobid" not in status


def test_reset_event_process_command_dispatch(tmp_path):
    project = tmp_path / "project"
    event = "S240413p"
    event_dir = project / "working" / event
    event_dir.mkdir(parents=True)
    (event_dir / "status.yaml").write_text(yaml.safe_dump({"jobid": "12345", "status": "removed"}))
    (project / "submitted_jobs.txt").write_text(f"{event}\n")

    result = process_command(project, {"action": "reset_event", "event": event})

    assert result["ok"] is True
    assert result["event"] == event
    assert result["command"]["action"] == "reset_event"


def test_reset_event_rejects_missing_event_dir(tmp_path):
    result = process_command(tmp_path / "project", {"action": "reset_event", "event": "missing"})

    assert result["ok"] is False
    assert "Event directory does not exist" in result["message"]
