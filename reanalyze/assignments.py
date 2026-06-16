"""Collaborative event assignment ledger support.

The assignment ledger is intentionally separate from the submission ledger.  A
project may point many independently running managers at one shared ledger file
using ``control/assignments.yaml``.  If all users can write that file through a
shared account or Linux group, managers can prevent duplicate event ownership
while still allowing everyone to monitor all events.
"""

from __future__ import annotations

from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any

import yaml

ASSIGNMENTS_CONFIG = "assignments.yaml"
DEFAULT_LEDGER = "assignment_ledger.json"
MUTATING_ACTIONS = {"submit_event", "hold_event", "release_event", "remove_event", "reset_event"}
ASSIGNMENT_ACTIONS = {"assign_event", "unassign_event"}


@dataclass(frozen=True)
class AssignmentDecision:
    event: str
    assignee: str | None
    operator: str | None
    allowed: bool
    reason: str


def config_path(project_dir: Path) -> Path:
    return project_dir / "control" / ASSIGNMENTS_CONFIG


def load_config(project_dir: Path) -> dict[str, Any]:
    path = config_path(project_dir)
    if not path.is_file():
        return {"enabled": False}
    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict):
        raise ValueError(f"assignment config must be a mapping: {path}")
    data.setdefault("enabled", True)
    return data


def ledger_path(project_dir: Path, config: dict[str, Any] | None = None) -> Path:
    config = config or load_config(project_dir)
    raw = config.get("ledger_path") or config.get("ledger") or project_dir / "control" / DEFAULT_LEDGER
    path = Path(str(raw)).expanduser()
    if not path.is_absolute():
        path = project_dir / path
    return path


def normalize_operator(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def current_operator(command: dict[str, Any] | None = None, config: dict[str, Any] | None = None) -> str | None:
    command = command or {}
    config = config or {}
    return (
        normalize_operator(command.get("operator"))
        or normalize_operator(command.get("user"))
        or normalize_operator(config.get("default_operator"))
        or normalize_operator(os.environ.get("PUROHIT_OPERATOR"))
        or normalize_operator(os.environ.get("USER"))
        or normalize_operator(os.environ.get("LOGNAME"))
    )


def admins(config: dict[str, Any]) -> set[str]:
    return {str(item).strip() for item in config.get("admins", []) or [] if str(item).strip()}


def _empty_ledger() -> dict[str, Any]:
    return {"version": 1, "events": {}, "updated_at": None}


def _read_ledger_unlocked(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size == 0:
        return _empty_ledger()
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid assignment ledger JSON: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"assignment ledger must be a JSON object: {path}")
    data.setdefault("version", 1)
    data.setdefault("events", {})
    if not isinstance(data["events"], dict):
        raise ValueError(f"assignment ledger events must be an object: {path}")
    return data


def read_ledger(project_dir: Path, config: dict[str, Any] | None = None) -> dict[str, Any]:
    return _read_ledger_unlocked(ledger_path(project_dir, config))


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False) as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")
        tmp = Path(handle.name)
    os.replace(tmp, path)
    try:
        path.chmod(0o664)
    except OSError:
        pass


def _locked_update(path: Path, updater) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        ledger = _read_ledger_unlocked(path)
        result = updater(ledger)
        ledger["updated_at"] = time.time()
        _atomic_write_json(path, ledger)
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    return result


def assignment_for_event(project_dir: Path, event: str, config: dict[str, Any] | None = None) -> dict[str, Any] | None:
    config = config or load_config(project_dir)
    if not config.get("enabled", False):
        return None
    item = read_ledger(project_dir, config).get("events", {}).get(event)
    return item if isinstance(item, dict) else None


def assignee_for_event(project_dir: Path, event: str, config: dict[str, Any] | None = None) -> str | None:
    item = assignment_for_event(project_dir, event, config)
    return normalize_operator(item.get("assignee")) if item else None


def assign_event(project_dir: Path, event: str, operator: str, *, force: bool = False, note: str | None = None) -> dict[str, Any]:
    config = load_config(project_dir)
    if not config.get("enabled", False):
        return {"ok": False, "event": event, "operator": operator, "message": "assignments are disabled"}
    if not operator:
        return {"ok": False, "event": event, "message": "operator is required to assign an event"}
    path = ledger_path(project_dir, config)

    def update(ledger: dict[str, Any]) -> dict[str, Any]:
        events = ledger.setdefault("events", {})
        existing = events.get(event)
        existing_assignee = normalize_operator(existing.get("assignee")) if isinstance(existing, dict) else None
        if existing_assignee and existing_assignee != operator and not force and operator not in admins(config):
            return {"ok": False, "event": event, "assignee": existing_assignee, "operator": operator, "message": f"event already assigned to {existing_assignee}"}
        item = {"assignee": operator, "assigned_at": time.time(), "assigned_by": operator}
        if note:
            item["note"] = note
        events[event] = item
        return {"ok": True, "event": event, "assignee": operator, "operator": operator, "message": "event assigned"}

    return _locked_update(path, update)


def unassign_event(project_dir: Path, event: str, operator: str | None, *, force: bool = False) -> dict[str, Any]:
    config = load_config(project_dir)
    if not config.get("enabled", False):
        return {"ok": False, "event": event, "operator": operator, "message": "assignments are disabled"}
    path = ledger_path(project_dir, config)

    def update(ledger: dict[str, Any]) -> dict[str, Any]:
        events = ledger.setdefault("events", {})
        existing = events.get(event)
        existing_assignee = normalize_operator(existing.get("assignee")) if isinstance(existing, dict) else None
        if not existing_assignee:
            return {"ok": True, "event": event, "operator": operator, "assignee": None, "message": "event was already unassigned"}
        if operator != existing_assignee and operator not in admins(config) and not force:
            return {"ok": False, "event": event, "operator": operator, "assignee": existing_assignee, "message": f"only {existing_assignee} or an admin can unassign this event"}
        events.pop(event, None)
        return {"ok": True, "event": event, "operator": operator, "assignee": None, "previous_assignee": existing_assignee, "message": "event unassigned"}

    return _locked_update(path, update)


def check_assignment(project_dir: Path, event: str, action: str, command: dict[str, Any] | None = None) -> AssignmentDecision:
    config = load_config(project_dir)
    operator = current_operator(command, config)
    if not config.get("enabled", False):
        return AssignmentDecision(event, None, operator, True, "assignments disabled")
    if action not in MUTATING_ACTIONS:
        return AssignmentDecision(event, None, operator, True, "non-mutating action")
    assignee = assignee_for_event(project_dir, event, config)
    if not assignee:
        if config.get("require_assignment_for_submit", True) and action == "submit_event":
            return AssignmentDecision(event, None, operator, False, "event must be assigned before submission")
        return AssignmentDecision(event, None, operator, True, "event unassigned")
    if operator == assignee or operator in admins(config):
        return AssignmentDecision(event, assignee, operator, True, "operator matches assignment")
    return AssignmentDecision(event, assignee, operator, False, f"event assigned to {assignee}, not {operator or 'unknown'}")


def assignment_metadata(project_dir: Path, event: str) -> dict[str, Any]:
    config = load_config(project_dir)
    if not config.get("enabled", False):
        return {"assignment_enabled": False, "assignee": None, "assignment_ledger": None}
    item = assignment_for_event(project_dir, event, config)
    return {"assignment_enabled": True, "assignee": normalize_operator(item.get("assignee")) if item else None, "assignment": item, "assignment_ledger": str(ledger_path(project_dir, config))}
