from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

from reanalyze import static_manager
from reanalyze.static_monitor import publish_once


def test_write_status_merges_status_yaml(tmp_path):
    event_dir = tmp_path / "project" / "working" / "S240001a"
    event_dir.mkdir(parents=True)
    (event_dir / "status.yaml").write_text(yaml.safe_dump({"jobid": "123"}))

    static_manager.write_status(event_dir, {"status": "held"})

    status = yaml.safe_load((event_dir / "status.yaml").read_text())
    assert status == {"jobid": "123", "status": "held"}


def test_process_submit_event_records_jobid_and_ledger(tmp_path, monkeypatch):
    project_dir = tmp_path / "project"
    event_dir = project_dir / "working" / "S240001a"
    event_dir.mkdir(parents=True)
    (event_dir / "config.ini").write_text("label=S240001a\n")

    def fake_run(command, check, capture_output, text):
        assert command[:2] == ["bilby_pipe", str(event_dir / "config.ini")]
        return subprocess.CompletedProcess(command, 0, stdout="1 job(s) submitted to cluster 12345.\n", stderr="")

    monkeypatch.setattr(static_manager.subprocess, "run", fake_run)

    result = static_manager.process_command(project_dir, {"action": "submit_event", "event": "S240001a"})

    assert result["ok"] is True
    assert result["jobid"] == "12345"
    assert (project_dir / "submitted_jobs.txt").read_text() == "S240001a\n"
    status = yaml.safe_load((event_dir / "status.yaml").read_text())
    assert status["jobid"] == "12345"
    assert status["status"] == "submitted"


def test_hold_release_remove_use_recorded_jobid(tmp_path, monkeypatch):
    project_dir = tmp_path / "project"
    event_dir = project_dir / "working" / "S240001a"
    event_dir.mkdir(parents=True)
    (event_dir / "status.yaml").write_text(yaml.safe_dump({"jobid": "12345", "status": "submitted"}))
    calls = []

    def fake_run(command, check, capture_output, text):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr(static_manager.subprocess, "run", fake_run)

    assert static_manager.process_command(project_dir, {"action": "hold_event", "event": "S240001a"})["ok"] is True
    assert static_manager.process_command(project_dir, {"action": "release_event", "event": "S240001a"})["ok"] is True
    assert static_manager.process_command(project_dir, {"action": "remove_event", "event": "S240001a"})["ok"] is True

    assert calls == [["condor_hold", "12345"], ["condor_release", "12345"], ["condor_rm", "12345"]]
    status = yaml.safe_load((event_dir / "status.yaml").read_text())
    assert status["status"] == "removed"


def test_publish_once_makes_static_files_web_readable(tmp_path):
    project_dir = tmp_path / "project"
    (project_dir / "working" / "S240001a").mkdir(parents=True)
    webdir = tmp_path / "public_html" / "monitor"

    publish_once(project_dir, webdir)

    assert (webdir / "index.html").stat().st_mode & 0o777 == 0o644
    assert (webdir / "status.json").stat().st_mode & 0o777 == 0o644
    assert webdir.stat().st_mode & 0o777 == 0o755


def test_publish_once_copies_event_outputs(tmp_path):
    project_dir = tmp_path / "project"
    event_dir = project_dir / "working" / "S240001a"
    plot = event_dir / "pe" / "result" / "trace.png"
    plot.parent.mkdir(parents=True)
    plot.write_text("fake png")
    webdir = tmp_path / "public_html" / "monitor"

    payload = publish_once(project_dir, webdir, copy_outputs=True)

    copied = webdir / "artifacts" / "S240001a" / "pe" / "result" / "trace.png"
    assert copied.read_text() == "fake png"
    assert payload["jobs"][0]["outputs"]
    assert payload["jobs"][0]["outputs"][0]["href"].endswith("trace.png")
