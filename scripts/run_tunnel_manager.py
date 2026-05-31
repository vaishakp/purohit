from __future__ import annotations

from pathlib import Path
import subprocess

import reanalyze.static_manager as _sm


def _run_checked(command: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=True, capture_output=True, text=True, cwd=cwd)


def _submit_manifest_event(project_dir: Path, event: str, status: dict | None = None) -> dict:
    if event in _sm.submitted_jobs(project_dir):
        return {"ok": False, "message": f"event {event} is already in submitted_jobs.txt"}
    status = status or _sm.read_yaml(_sm.event_dir(project_dir, event) / "status.yaml")
    submit_file_value = status.get("submit_file")
    if not submit_file_value:
        raise FileNotFoundError(
            f"Manifest event {event!r} has no submit_file in status.yaml. "
            "Prepare the event with ManifestRerun/write_submit_file before using web Submit."
        )
    submit_file = _sm.resolve_event_path(project_dir, event, submit_file_value)
    if not submit_file.is_file():
        raise FileNotFoundError(f"Manifest submit file for event {event!r} does not exist: {submit_file}")
    out = _run_checked(["condor_submit", str(submit_file)], cwd=submit_file.parent)
    jobid = _sm.parse_cluster_id(out.stdout)
    _sm.append_submitted(project_dir, event)
    updates = {
        "jobid": jobid,
        "status": "submitted",
        "submit_file": str(submit_file),
        "workflow_type": status.get("workflow_type", "manifest"),
        "application": status.get("application", "manifest"),
    }
    for key in ("config", "submit_ini", "submitted_config", "output", "manifest", "command_template"):
        if key in status:
            updates[key] = status[key]
    _sm.write_status(_sm.event_dir(project_dir, event), updates)
    return {
        "ok": True,
        "event": event,
        "jobid": jobid,
        "stdout": out.stdout,
        "stderr": out.stderr,
        "submit_file": str(submit_file),
        "workflow_type": updates["workflow_type"],
        "application": updates["application"],
    }


def _submit_bilby_event(project_dir: Path, event: str) -> dict:
    if event in _sm.submitted_jobs(project_dir):
        return {"ok": False, "message": f"event {event} is already in submitted_jobs.txt"}
    config = _sm.find_event_config(project_dir, event)
    staged = _sm.stage_bilby_inputs(project_dir, event, config)
    submit_config = staged.config_path
    out = _run_checked(["bilby_pipe", str(submit_config), "--submit"], cwd=project_dir)
    jobid = _sm.parse_cluster_id(out.stdout)
    _sm.append_submitted(project_dir, event)
    updates = {
        "jobid": jobid,
        "status": "submitted",
        "submitted_config": str(submit_config),
        "workflow_type": "bilby_pipe",
        "application": "bilby",
    }
    if staged.enabled:
        updates.update({
            "staged_config": str(submit_config),
            "input_manifest": None if staged.manifest_path is None else str(staged.manifest_path),
            "staged_input_count": len(staged.copied_files),
        })
    _sm.write_status(_sm.event_dir(project_dir, event), updates)
    return {
        "ok": True,
        "event": event,
        "jobid": jobid,
        "stdout": out.stdout,
        "stderr": out.stderr,
        "staging_enabled": staged.enabled,
        "staged_config": str(submit_config),
        "input_manifest": None if staged.manifest_path is None else str(staged.manifest_path),
        "staged_input_count": len(staged.copied_files),
        "workflow_type": "bilby_pipe",
        "application": "bilby",
    }


_sm.run_checked = _run_checked
_sm.submit_manifest_event = _submit_manifest_event
_sm.submit_bilby_event = _submit_bilby_event

from reanalyze.tunnel_webapp import main  # noqa: E402


if __name__ == "__main__":
    main()
