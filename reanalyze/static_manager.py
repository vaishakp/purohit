"""Background command manager for the static Purohit web interface.

The manager runs on the submit/login side and periodically consumes a JSON
command file. This lets a PESummary-style static webdir expose job-management
instructions without running an inbound web server.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from typing import Any

import yaml

from reanalyze.input_staging import stage_bilby_inputs
from reanalyze.static_monitor import publish_once

SUPPORTED_ACTIONS = {"submit_event", "hold_event", "release_event", "remove_event", "reset_event", "refresh"}
RESETTABLE_OUTPUT_DIRS = ("pe",)
RESETTABLE_PATTERNS = (
    "*.dag.*",
    "*.rescue*",
    "*.lock",
    "condor.out",
    "condor.err",
    "condor.log",
)
GENERATED_CONFIG_SUFFIXES = (".staged.ini", ".gwave.ini", ".target.ini", ".source.ini")


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"commands": []}
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        return {"commands": [], "error": f"invalid JSON: {exc}"}
    if isinstance(data, list):
        return {"commands": data}
    if isinstance(data, dict):
        commands = data.get("commands", [])
        return {"commands": commands if isinstance(commands, list) else []}
    return {"commands": [], "error": "command file must be an object or list"}


def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False) as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")
        tmp = Path(handle.name)
    os.replace(tmp, path)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def append_audit(project_dir: Path, record: dict[str, Any]) -> None:
    audit_path = project_dir / "control" / "audit.jsonl"
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    record = {"timestamp": time.time(), **record}
    with audit_path.open("a") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    with path.open("r") as handle:
        value = yaml.safe_load(handle) or {}
    return value if isinstance(value, dict) else {}


def write_status(event_dir: Path, updates: dict[str, Any]) -> None:
    status_path = event_dir / "status.yaml"
    status = read_yaml(status_path)
    status.update(updates)
    status_path.write_text(yaml.safe_dump(status, sort_keys=False))


def submitted_jobs(project_dir: Path) -> list[str]:
    ledger = project_dir / "submitted_jobs.txt"
    if not ledger.is_file():
        return []
    return [line.strip() for line in ledger.read_text().splitlines() if line.strip()]


def append_submitted(project_dir: Path, event: str) -> None:
    ledger = project_dir / "submitted_jobs.txt"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    existing = set(submitted_jobs(project_dir))
    if event not in existing:
        with ledger.open("a") as handle:
            handle.write(f"{event}\n")


def remove_submitted(project_dir: Path, event: str) -> bool:
    ledger = project_dir / "submitted_jobs.txt"
    if not ledger.is_file():
        return False
    entries = submitted_jobs(project_dir)
    filtered = [item for item in entries if item != event]
    if filtered == entries:
        return False
    backup = ledger.with_name(f"{ledger.name}.bak.{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(ledger, backup)
    ledger.write_text("".join(f"{item}\n" for item in filtered))
    return True


def parse_cluster_id(stdout: str) -> str:
    import re

    cluster = re.search(r"cluster\s+(\d+)(?:\.\d+)?", stdout, re.IGNORECASE)
    if cluster:
        return cluster.group(1)
    matches = re.findall(r"\b(\d+)(?:\.\d+)?\b", stdout)
    if matches:
        return matches[-1]
    raise RuntimeError(f"Could not parse Condor cluster id from output:\n{stdout}")


def event_dir(project_dir: Path, event: str) -> Path:
    return project_dir / "working" / event


def resolve_event_path(project_dir: Path, event: str, value: Any) -> Path:
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        path = event_dir(project_dir, event) / path
    return path


def is_manifest_event(status: dict[str, Any]) -> bool:
    workflow = str(status.get("workflow_type") or "").lower()
    return workflow == "manifest" or bool(status.get("submit_file"))


def find_event_config(project_dir: Path, event: str) -> Path:
    edir = event_dir(project_dir, event)
    status = read_yaml(edir / "status.yaml")
    submit_ini = status.get("submit_ini")
    if submit_ini:
        path = Path(str(submit_ini)).expanduser()
        if not path.is_absolute():
            path = edir / path
        if path.is_file():
            return path
        raise FileNotFoundError(f"submit_ini recorded for event {event!r} does not exist: {path}")
    candidates = sorted(path for path in edir.glob("*.ini") if not any(path.name.endswith(suffix) for suffix in GENERATED_CONFIG_SUFFIXES))
    if not candidates:
        generated = sorted(edir.glob("*.ini"))
        if generated:
            raise FileNotFoundError(f"Only generated INIs found for event {event!r}; set status.yaml submit_ini explicitly. Candidates: {[str(p) for p in generated]}")
        raise FileNotFoundError(f"No copied INI found for event {event!r}")
    return candidates[0]


def run_checked(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=True, capture_output=True, text=True)


def command_display(command: Any) -> str:
    if isinstance(command, (list, tuple)):
        return " ".join(str(part) for part in command)
    return str(command)


def called_process_error_result(exc: subprocess.CalledProcessError, *, event: str | None, action: str | None) -> dict[str, Any]:
    command_line = [str(part) for part in exc.cmd] if isinstance(exc.cmd, (list, tuple)) else str(exc.cmd)
    return {
        "ok": False,
        "event": event,
        "action": action,
        "returncode": exc.returncode,
        "command_line": command_line,
        "stdout": exc.stdout or "",
        "stderr": exc.stderr or "",
        "message": f"Command failed with exit status {exc.returncode}: {command_display(exc.cmd)}",
    }


def submit_manifest_event(project_dir: Path, event: str, status: dict[str, Any] | None = None) -> dict[str, Any]:
    if event in submitted_jobs(project_dir):
        return {"ok": False, "message": f"event {event} is already in submitted_jobs.txt"}
    status = status or read_yaml(event_dir(project_dir, event) / "status.yaml")
    submit_file_value = status.get("submit_file")
    if not submit_file_value:
        raise FileNotFoundError(
            f"Manifest event {event!r} has no submit_file in status.yaml. "
            "Prepare the event with ManifestRerun/write_submit_file before using web Submit."
        )
    submit_file = resolve_event_path(project_dir, event, submit_file_value)
    if not submit_file.is_file():
        raise FileNotFoundError(f"Manifest submit file for event {event!r} does not exist: {submit_file}")
    out = run_checked(["condor_submit", str(submit_file)])
    jobid = parse_cluster_id(out.stdout)
    append_submitted(project_dir, event)
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
    write_status(event_dir(project_dir, event), updates)
    return {"ok": True, "event": event, "jobid": jobid, "stdout": out.stdout, "stderr": out.stderr, "submit_file": str(submit_file), "workflow_type": updates["workflow_type"], "application": updates["application"]}


def submit_bilby_event(project_dir: Path, event: str) -> dict[str, Any]:
    if event in submitted_jobs(project_dir):
        return {"ok": False, "message": f"event {event} is already in submitted_jobs.txt"}
    config = find_event_config(project_dir, event)
    staged = stage_bilby_inputs(project_dir, event, config)
    submit_config = staged.config_path
    out = run_checked(["bilby_pipe", str(submit_config), "--submit"])
    jobid = parse_cluster_id(out.stdout)
    append_submitted(project_dir, event)
    updates = {"jobid": jobid, "status": "submitted", "submitted_config": str(submit_config), "workflow_type": "bilby_pipe", "application": "bilby"}
    if staged.enabled:
        updates.update({"staged_config": str(submit_config), "input_manifest": None if staged.manifest_path is None else str(staged.manifest_path), "staged_input_count": len(staged.copied_files)})
    write_status(event_dir(project_dir, event), updates)
    return {"ok": True, "event": event, "jobid": jobid, "stdout": out.stdout, "stderr": out.stderr, "staging_enabled": staged.enabled, "staged_config": str(submit_config), "input_manifest": None if staged.manifest_path is None else str(staged.manifest_path), "staged_input_count": len(staged.copied_files), "workflow_type": "bilby_pipe", "application": "bilby"}


def submit_event(project_dir: Path, event: str) -> dict[str, Any]:
    status = read_yaml(event_dir(project_dir, event) / "status.yaml")
    if is_manifest_event(status):
        return submit_manifest_event(project_dir, event, status=status)
    return submit_bilby_event(project_dir, event)


def jobid_for_event(project_dir: Path, event: str) -> str:
    status = read_yaml(event_dir(project_dir, event) / "status.yaml")
    jobid = status.get("jobid")
    if jobid in (None, ""):
        raise ValueError(f"No jobid recorded for event {event!r}")
    return str(jobid)


def hold_event(project_dir: Path, event: str) -> dict[str, Any]:
    jobid = jobid_for_event(project_dir, event)
    out = run_checked(["condor_hold", jobid])
    write_status(event_dir(project_dir, event), {"status": "held"})
    return {"ok": True, "event": event, "jobid": jobid, "stdout": out.stdout, "stderr": out.stderr}


def release_event(project_dir: Path, event: str) -> dict[str, Any]:
    jobid = jobid_for_event(project_dir, event)
    out = run_checked(["condor_release", jobid])
    write_status(event_dir(project_dir, event), {"status": "submitted"})
    return {"ok": True, "event": event, "jobid": jobid, "stdout": out.stdout, "stderr": out.stderr}


def remove_event(project_dir: Path, event: str) -> dict[str, Any]:
    jobid = jobid_for_event(project_dir, event)
    out = run_checked(["condor_rm", jobid])
    write_status(event_dir(project_dir, event), {"status": "removed"})
    return {"ok": True, "event": event, "jobid": jobid, "stdout": out.stdout, "stderr": out.stderr}


def _safe_remove_path(path: Path, event_directory: Path) -> str | None:
    resolved = path.resolve()
    event_resolved = event_directory.resolve()
    if resolved == event_resolved or event_resolved not in resolved.parents:
        raise ValueError(f"Refusing to remove path outside event directory: {path}")
    if not path.exists():
        return None
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()
    return str(path)


def reset_event(project_dir: Path, event: str) -> dict[str, Any]:
    edir = event_dir(project_dir, event)
    if not edir.is_dir():
        raise FileNotFoundError(f"Event directory does not exist: {edir}")

    removed_paths: list[str] = []
    for name in RESETTABLE_OUTPUT_DIRS:
        removed = _safe_remove_path(edir / name, edir)
        if removed:
            removed_paths.append(removed)
    for pattern in RESETTABLE_PATTERNS:
        for path in sorted(edir.glob(pattern)):
            removed = _safe_remove_path(path, edir)
            if removed:
                removed_paths.append(removed)

    ledger_changed = remove_submitted(project_dir, event)
    status_path = edir / "status.yaml"
    status = read_yaml(status_path)
    previous_jobid = status.pop("jobid", None)
    status.update({
        "status": "pending",
        "note": "Reset by static manager; ready for fresh web submission",
        "reset_at": time.time(),
        "previous_jobid": previous_jobid,
    })
    status_path.write_text(yaml.safe_dump(status, sort_keys=False))
    return {
        "ok": True,
        "event": event,
        "status": "pending",
        "removed_from_submitted_jobs": ledger_changed,
        "previous_jobid": previous_jobid,
        "removed_paths": removed_paths,
        "message": "event reset; click Submit to submit a fresh job",
    }


def process_command(project_dir: Path, command: dict[str, Any]) -> dict[str, Any]:
    action = command.get("action")
    event = command.get("event")
    if action not in SUPPORTED_ACTIONS:
        return {"ok": False, "command": command, "message": f"unsupported action {action!r}"}
    if action == "refresh":
        return {"ok": True, "command": command, "message": "refresh requested"}
    if not isinstance(event, str) or not event:
        return {"ok": False, "command": command, "message": "event is required"}
    try:
        if action == "submit_event":
            result = submit_event(project_dir, event)
        elif action == "hold_event":
            result = hold_event(project_dir, event)
        elif action == "release_event":
            result = release_event(project_dir, event)
        elif action == "remove_event":
            result = remove_event(project_dir, event)
        elif action == "reset_event":
            result = reset_event(project_dir, event)
        else:  # pragma: no cover - guarded by SUPPORTED_ACTIONS
            result = {"ok": False, "message": f"unhandled action {action}"}
    except subprocess.CalledProcessError as exc:
        result = called_process_error_result(exc, event=event, action=action)
    except Exception as exc:  # noqa: BLE001 - operational command audit should record failures
        result = {"ok": False, "event": event, "action": action, "message": str(exc)}
    result["command"] = command
    return result


def process_command_file(project_dir: Path, command_file: Path) -> list[dict[str, Any]]:
    payload = read_json(command_file)
    commands = payload.get("commands", [])
    results: list[dict[str, Any]] = []
    for command in commands:
        if not isinstance(command, dict):
            result = {"ok": False, "command": command, "message": "command must be an object"}
        else:
            result = process_command(project_dir, command)
        append_audit(project_dir, result)
        results.append(result)
    if commands or payload.get("error"):
        archive_dir = project_dir / "control" / "processed"
        archive_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        if command_file.is_file():
            shutil.copy2(command_file, archive_dir / f"commands-{stamp}.json")
        atomic_write_json(command_file, {"commands": []})
    return results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Purohit static webdir manager.")
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument("--webdir", required=True, type=Path)
    parser.add_argument("--command-file", type=Path, default=None, help="JSON command file. Defaults to project_dir/control/commands.json.")
    parser.add_argument("--interval", type=int, default=60, help="Command processing/status refresh interval in seconds.")
    parser.add_argument("--plot-interval", type=int, default=300, help="Minimum seconds between output artifact copy passes.")
    parser.add_argument("--once", action="store_true", help="Process commands and publish once, then exit.")
    parser.add_argument("--no-history", action="store_true")
    parser.add_argument("--heartbeat-filename", default="heartbeat.json")
    parser.add_argument("--max-artifacts-per-event", type=int, default=40)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    project_dir = args.project_dir.expanduser().resolve()
    webdir = args.webdir.expanduser().resolve()
    command_file = (args.command_file or project_dir / "control" / "commands.json").expanduser().resolve()
    command_file.parent.mkdir(parents=True, exist_ok=True)
    if not command_file.exists():
        atomic_write_json(command_file, {"commands": []})

    last_plot_publish = 0.0
    while True:
        results = process_command_file(project_dir, command_file)
        now = time.time()
        copy_outputs = now - last_plot_publish >= args.plot_interval
        publish_once(
            project_dir,
            webdir,
            include_history=not args.no_history,
            heartbeat_filename=args.heartbeat_filename,
            copy_outputs=copy_outputs,
            command_file=command_file,
            max_artifacts_per_event=args.max_artifacts_per_event,
        )
        if copy_outputs:
            last_plot_publish = now
        print(f"Processed {len(results)} command(s); published manager page to {webdir} at {time.strftime('%Y-%m-%d %H:%M:%S')}")
        if args.once:
            return
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
