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

from reanalyze.static_monitor import publish_once

SUPPORTED_ACTIONS = {"submit_event", "hold_event", "release_event", "remove_event", "refresh"}


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


def find_event_config(project_dir: Path, event: str) -> Path:
    candidates = sorted(event_dir(project_dir, event).glob("*.ini"))
    if not candidates:
        raise FileNotFoundError(f"No copied INI found for event {event!r}")
    return candidates[0]


def run_checked(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=True, capture_output=True, text=True)


def submit_event(project_dir: Path, event: str) -> dict[str, Any]:
    if event in submitted_jobs(project_dir):
        return {"ok": False, "message": f"event {event} is already in submitted_jobs.txt"}
    config = find_event_config(project_dir, event)
    out = run_checked(["bilby_pipe", str(config), "--submit"])
    jobid = parse_cluster_id(out.stdout)
    append_submitted(project_dir, event)
    write_status(event_dir(project_dir, event), {"jobid": jobid, "status": "submitted"})
    return {"ok": True, "event": event, "jobid": jobid, "stdout": out.stdout}


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
    return {"ok": True, "event": event, "jobid": jobid, "stdout": out.stdout}


def release_event(project_dir: Path, event: str) -> dict[str, Any]:
    jobid = jobid_for_event(project_dir, event)
    out = run_checked(["condor_release", jobid])
    write_status(event_dir(project_dir, event), {"status": "submitted"})
    return {"ok": True, "event": event, "jobid": jobid, "stdout": out.stdout}


def remove_event(project_dir: Path, event: str) -> dict[str, Any]:
    jobid = jobid_for_event(project_dir, event)
    out = run_checked(["condor_rm", jobid])
    write_status(event_dir(project_dir, event), {"status": "removed"})
    return {"ok": True, "event": event, "jobid": jobid, "stdout": out.stdout}


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
        else:  # pragma: no cover - guarded by SUPPORTED_ACTIONS
            result = {"ok": False, "message": f"unhandled action {action}"}
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
