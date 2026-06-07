from __future__ import annotations

import importlib
from pathlib import Path

import yaml


def test_ci_imports_core_modules_without_runtime_condor_packages():
    modules = [
        "reanalyze.central_manager",
        "reanalyze.input_staging",
        "reanalyze.manager_health",
        "reanalyze.project_init",
        "reanalyze.remote_import",
        "reanalyze.static_manager",
        "reanalyze.static_monitor",
        "reanalyze.tunnel_manager",
        "reanalyze.utils",
    ]

    for module in modules:
        importlib.import_module(module)


def test_static_monitor_falls_back_when_condor_commands_are_missing(tmp_path, monkeypatch):
    from reanalyze import static_monitor

    project = tmp_path / "project"
    event_dir = project / "working" / "S1"
    event_dir.mkdir(parents=True)
    (event_dir / "status.yaml").write_text(yaml.safe_dump({"jobid": "12345", "status": "submitted"}))

    def fake_run(*args, **kwargs):
        raise FileNotFoundError("condor command not installed")

    monkeypatch.setattr(static_monitor.subprocess, "run", fake_run)

    rows = static_monitor.collect_jobs(project, include_history=True)

    assert len(rows) == 1
    assert rows[0]["event"] == "S1"
    assert rows[0]["status"] == "submitted"
    assert rows[0]["source"] == "local"
    assert rows[0]["note"] == "not found in condor_q"


def test_static_monitor_marks_completed_result_without_condor(tmp_path, monkeypatch):
    from reanalyze import static_monitor

    project = tmp_path / "project"
    event_dir = project / "working" / "S1"
    final_result = event_dir / "pe" / "final_result"
    final_result.mkdir(parents=True)
    (final_result / "result.hdf5").write_text("fake result\n")
    (event_dir / "status.yaml").write_text(yaml.safe_dump({"jobid": "12345", "status": "submitted"}))

    def fake_run(*args, **kwargs):
        raise FileNotFoundError("condor command not installed")

    monkeypatch.setattr(static_monitor.subprocess, "run", fake_run)

    rows = static_monitor.collect_jobs(project, include_history=True)

    assert rows[0]["status"] == "completed"
    assert rows[0]["note"] == "not in condor_q; final result found"


def test_manager_health_reports_missing_condor_binaries_without_failing(tmp_path, monkeypatch):
    from reanalyze import manager_health

    monkeypatch.setattr(manager_health.shutil, "which", lambda executable: None)

    checks = manager_health.collect_environment_checks(tmp_path / "project", tmp_path / "web")

    for executable in ("bilby_pipe", "condor_q", "condor_hold", "condor_release", "condor_rm"):
        check = checks[f"{executable}_on_path"]
        assert check == {"ok": False, "value": None}
    assert checks["project_dir_writable"]["ok"] is True
    assert checks["webdir_writable"]["ok"] is True
