"""Command-queue support for controlled Purohit job submission.

This module intentionally implements a narrow control plane. It only accepts
allowlisted JSON commands and never executes arbitrary shell text from a command
file. The primary command is ``submit_event`` for submitting a selected pending
event from an existing project-local INI file.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from typing import Any

import yaml


VALID_EVENT_RE = re.compile(r"^[A-Za-z0-9_.:+-]+$")
VALID_ACTIONS = {"submit_event", "refresh_status"}


class ControlCommandError(RuntimeError):
    """Raised when a control command is invalid or cannot be executed."""


def canonical_payload(command: dict[str, Any]) -> bytes:
    """Return canonical JSON bytes for HMAC signing.

    The ``signature`` key itself is excluded from the signed payload.
    """

    payload = {key: value for key, value in command.items() if key != "signature"}
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def read_secret(secret_file: Path | None) -> bytes | None:
    """Read an optional shared secret from disk."""

    if secret_file is None:
        return None
    value = secret_file.expanduser().read_text().strip()
    if not value:
        raise ControlCommandError(f"Control secret file is empty: {secret_file}")
    return value.encode("utf-8")


def verify_signature(command: dict[str, Any], secret: bytes | None, allow_unsigned: bool = False) -> None:
    """Validate a command signature unless unsigned commands are explicitly allowed."""

    if secret is None:
        if allow_unsigned:
            return
        raise ControlCommandError("Unsigned control commands are disabled; provide --control-secret-file or --allow-unsigned-control")

    signature = command.get("signature")
    if not isinstance(signature, str) or not signature:
        raise ControlCommandError("Missing command signature")

    expected = hmac.new(secret, canonical_payload(command), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise ControlCommandError("Invalid command signature")


def load_command(path: Path) -> dict[str, Any]:
    """Load one JSON command file."""

    try:
        command = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ControlCommandError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(command, dict):
        raise ControlCommandError(f"Command is not a JSON object: {path}")
    return command


def validate_base_command(command: dict[str, Any]) -> str:
    """Validate common command fields and return the action."""

    action = command.get("action")
    if action not in VALID_ACTIONS:
        raise ControlCommandError(f"Unsupported action: {action!r}")
    return str(action)


def parse_jobid_from_stdout(stdout: str) -> str:
    """Parse a Condor cluster id from bilby_pipe submission output."""

    cluster_match = re.search(r"cluster\s+(\d+)(?:\.\d+)?", stdout, re.IGNORECASE)
    if cluster_match is not None:
        return cluster_match.group(1)

    matches = re.findall(r"\b(\d+)(?:\.\d+)?\b", stdout)
    if matches:
        return matches[-1]

    raise ControlCommandError(f"Could not parse Condor cluster id from bilby_pipe output:\n{stdout}")


def read_submitted_jobs(project_dir: Path) -> list[str]:
    """Read the project submitted-jobs ledger."""

    ledger = project_dir / "submitted_jobs.txt"
    if not ledger.is_file():
        return []
    return [line.strip() for line in ledger.read_text().splitlines() if line.strip()]


def append_submitted_job(project_dir: Path, event: str) -> None:
    """Append one event to the submitted-jobs ledger if it is not already present."""

    submitted = set(read_submitted_jobs(project_dir))
    if event in submitted:
        return
    ledger = project_dir / "submitted_jobs.txt"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a") as handle:
        handle.write(f"{event}\n")


def update_status_yaml(event_dir: Path, info: dict[str, Any]) -> None:
    """Merge information into an event ``status.yaml`` file."""

    status_path = event_dir / "status.yaml"
    if status_path.is_file():
        with status_path.open("r") as handle:
            status = yaml.safe_load(handle) or {}
    else:
        status = {}
    if not isinstance(status, dict):
        status = {}
    status.update(info)
    with status_path.open("w") as handle:
        yaml.safe_dump(status, handle, sort_keys=False)


def resolve_event_config(project_dir: Path, event: str, requested_config: str | None = None) -> Path:
    """Resolve the INI file for an event under ``project_dir/working/<event>``."""

    if not VALID_EVENT_RE.fullmatch(event):
        raise ControlCommandError(f"Invalid event name: {event!r}")

    event_dir = (project_dir / "working" / event).resolve()
    if not event_dir.is_dir():
        raise ControlCommandError(f"Event directory does not exist: {event_dir}")

    if requested_config:
        config_path = Path(requested_config).expanduser().resolve()
        try:
            config_path.relative_to(event_dir)
        except ValueError as exc:
            raise ControlCommandError("Requested config path is outside the event directory") from exc
        if not config_path.is_file() or config_path.suffix != ".ini":
            raise ControlCommandError(f"Requested config is not an INI file: {config_path}")
        return config_path

    configs = sorted(event_dir.glob("*.ini"))
    if not configs:
        raise ControlCommandError(f"No INI files found for event {event!r} in {event_dir}")
    if len(configs) > 1:
        raise ControlCommandError(
            f"Multiple INI files found for event {event!r}; include config_path in the command"
        )
    return configs[0]


def submit_event(project_dir: Path, event: str, config_path: str | None = None) -> dict[str, Any]:
    """Submit one selected pending event using its project-local INI file."""

    project_dir = project_dir.expanduser().resolve()
    submitted = set(read_submitted_jobs(project_dir))
    if event in submitted:
        raise ControlCommandError(f"Event {event!r} has already been submitted")

    ini_path = resolve_event_config(project_dir, event, requested_config=config_path)
    command = ["bilby_pipe", str(ini_path), "--submit"]
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise ControlCommandError("Could not find bilby_pipe on PATH") from exc
    except subprocess.CalledProcessError as exc:
        raise ControlCommandError(
            f"bilby_pipe submission failed with exit code {exc.returncode}:\n{exc.stderr or exc.stdout}"
        ) from exc

    jobid = parse_jobid_from_stdout(result.stdout)
    event_dir = project_dir / "working" / event
    append_submitted_job(project_dir, event)
    update_status_yaml(event_dir, {"jobid": jobid, "status": "submitted"})

    return {
        "event": event,
        "jobid": jobid,
        "config_path": str(ini_path),
        "stdout": result.stdout,
    }


def audit(project_dir: Path, record: dict[str, Any]) -> None:
    """Append an audit record to ``project_dir/control/audit.jsonl``."""

    audit_dir = project_dir / "control"
    audit_dir.mkdir(parents=True, exist_ok=True)
    record = dict(record)
    record.setdefault("timestamp", time.time())
    with (audit_dir / "audit.jsonl").open("a") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def move_command(path: Path, destination_dir: Path, status: str) -> Path:
    """Move a command file into processed/rejected storage with a status suffix."""

    destination_dir.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%dT%H%M%S")
    destination = destination_dir / f"{path.stem}.{timestamp}.{status}{path.suffix}"
    shutil.move(str(path), str(destination))
    return destination


def handle_command(project_dir: Path, command: dict[str, Any]) -> dict[str, Any]:
    """Execute one validated command and return a result record."""

    action = validate_base_command(command)
    if action == "refresh_status":
        return {"action": action, "result": "ok"}

    if action == "submit_event":
        event = command.get("event")
        if not isinstance(event, str) or not event:
            raise ControlCommandError("submit_event requires a non-empty event field")
        result = submit_event(project_dir, event, config_path=command.get("config_path"))
        return {"action": action, "result": "submitted", **result}

    raise ControlCommandError(f"Unsupported action: {action!r}")


def process_command_queue(
    project_dir: Path,
    inbox_dir: Path,
    processed_dir: Path | None = None,
    rejected_dir: Path | None = None,
    secret_file: Path | None = None,
    allow_unsigned: bool = False,
    max_commands: int = 10,
) -> list[dict[str, Any]]:
    """Process queued control commands.

    Parameters
    ----------
    project_dir : pathlib.Path
        Purohit project directory.
    inbox_dir : pathlib.Path
        Directory containing JSON command files.
    processed_dir, rejected_dir : pathlib.Path, optional
        Destination directories for processed and rejected command files.
    secret_file : pathlib.Path, optional
        Shared secret file used to verify HMAC-SHA256 command signatures.
    allow_unsigned : bool, optional
        If true, process unsigned command files. This should only be used in a
        trusted local command queue.
    max_commands : int, optional
        Maximum number of command files to process in one poll cycle.
    """

    project_dir = project_dir.expanduser().resolve()
    inbox_dir = inbox_dir.expanduser().resolve()
    processed_dir = (processed_dir or inbox_dir.parent / "processed").expanduser().resolve()
    rejected_dir = (rejected_dir or inbox_dir.parent / "rejected").expanduser().resolve()
    secret = read_secret(secret_file.expanduser().resolve() if secret_file else None)

    if not inbox_dir.is_dir():
        return []

    results: list[dict[str, Any]] = []
    for path in sorted(inbox_dir.glob("*.json"))[:max_commands]:
        record: dict[str, Any] = {"command_file": str(path), "ok": False}
        try:
            command = load_command(path)
            verify_signature(command, secret, allow_unsigned=allow_unsigned)
            result = handle_command(project_dir, command)
            record.update({"ok": True, "command": command, "result": result})
            move_command(path, processed_dir, "processed")
        except Exception as exc:  # noqa: BLE001 - audit all rejection causes
            record.update({"error": str(exc)})
            move_command(path, rejected_dir, "rejected")
        audit(project_dir, record)
        results.append(record)

    return results
