"""Collaborative event assignment helpers for Purohit."""

from __future__ import annotations

from dataclasses import dataclass
import getpass
import hashlib
from pathlib import Path
import re
from typing import Any

import yaml

DEFAULT_ASSIGNMENTS_FILENAME = "assignments.yaml"
MUTATING_ACTIONS = {"submit_event", "hold_event", "release_event", "remove_event", "reset_event"}


@dataclass(frozen=True)
class AssignmentDecision:
    event: str
    assigned_to: str | None
    source: str | None
    reason: str | None = None


def default_assignment_path(project_dir: Path) -> Path:
    return project_dir / "control" / DEFAULT_ASSIGNMENTS_FILENAME


def load_assignments(project_dir: Path, assignment_file: Path | None = None) -> dict[str, Any]:
    path = (assignment_file or default_assignment_path(project_dir)).expanduser()
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text()) or {}
    return data if isinstance(data, dict) else {}


def _user_name(item: Any) -> str | None:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        value = item.get("name") or item.get("user") or item.get("username")
        return str(value) if value else None
    return None


def assignment_users(config: dict[str, Any]) -> list[str]:
    users = config.get("users", [])
    if isinstance(users, dict):
        return [str(name) for name in users.keys()]
    if isinstance(users, list):
        out = [_user_name(item) for item in users]
        return [item for item in out if item]
    return []


def admin_users(config: dict[str, Any]) -> set[str]:
    admins = set(str(item) for item in config.get("admins", []) or [])
    users = config.get("users", [])
    if isinstance(users, dict):
        for name, meta in users.items():
            if isinstance(meta, dict) and meta.get("admin"):
                admins.add(str(name))
    elif isinstance(users, list):
        for item in users:
            if isinstance(item, dict) and item.get("admin"):
                name = _user_name(item)
                if name:
                    admins.add(name)
    return admins


def current_operator(default: str | None = None) -> str:
    return default or getpass.getuser()


def operator_from_command(command: dict[str, Any] | None = None, default: str | None = None) -> str:
    if command:
        for key in ("operator", "user", "actor"):
            value = command.get(key)
            if value:
                return str(value)
    return current_operator(default)


def _explicit_event_assignment(events: Any, event: str) -> AssignmentDecision | None:
    if not isinstance(events, dict) or event not in events:
        return None
    value = events[event]
    if isinstance(value, str):
        return AssignmentDecision(event=event, assigned_to=value, source="manual", reason="events override")
    if isinstance(value, dict):
        assigned_to = value.get("assigned_to") or value.get("user") or value.get("owner")
        if assigned_to:
            return AssignmentDecision(event=event, assigned_to=str(assigned_to), source="manual", reason=str(value.get("reason") or "events override"))
    return AssignmentDecision(event=event, assigned_to=None, source="manual", reason="explicitly unassigned")


def _event_month(event: str) -> int | None:
    match = re.match(r"^[A-Za-z]?(\d{2})(\d{2})(\d{2})", event)
    if not match:
        return None
    month = int(match.group(2))
    return month if 1 <= month <= 12 else None


def _stable_index(event: str, n_users: int) -> int:
    digest = hashlib.sha256(event.encode("utf-8")).hexdigest()
    return int(digest[:12], 16) % n_users


def assign_event(event: str, config: dict[str, Any]) -> AssignmentDecision:
    explicit = _explicit_event_assignment(config.get("events"), event)
    if explicit is not None:
        return explicit
    users = assignment_users(config)
    if not users:
        return AssignmentDecision(event=event, assigned_to=None, source=None, reason="no assignment users configured")
    policy = config.get("policy", {}) or {}
    if isinstance(policy, str):
        policy = {"mode": policy}
    mode = str(policy.get("mode", "hash"))
    if mode == "manual":
        return AssignmentDecision(event=event, assigned_to=None, source="manual", reason="no manual override")
    if mode == "month":
        month_owners = policy.get("month_owners") or policy.get("months") or {}
        month = _event_month(event)
        if month is not None and isinstance(month_owners, dict):
            for key in (f"{month:02d}", str(month)):
                if key in month_owners:
                    return AssignmentDecision(event=event, assigned_to=str(month_owners[key]), source="month", reason=f"month {month:02d}")
        if month is not None:
            return AssignmentDecision(event=event, assigned_to=users[(month - 1) % len(users)], source="month", reason=f"month {month:02d}")
    if mode in {"hash", "round_robin", "stable_round_robin"}:
        return AssignmentDecision(event=event, assigned_to=users[_stable_index(event, len(users))], source=mode, reason="stable hash")
    return AssignmentDecision(event=event, assigned_to=None, source=mode, reason=f"unknown assignment policy {mode!r}")


def assignment_for_event(project_dir: Path, event: str, assignment_file: Path | None = None) -> AssignmentDecision:
    return assign_event(event, load_assignments(project_dir, assignment_file=assignment_file))


def check_command_authorized(project_dir: Path, command: dict[str, Any], *, actor: str | None = None, assignment_file: Path | None = None) -> tuple[bool, dict[str, Any]]:
    action = command.get("action")
    event = command.get("event")
    operator = operator_from_command(command, default=actor)
    config = load_assignments(project_dir, assignment_file=assignment_file)
    admins = admin_users(config)
    if action not in MUTATING_ACTIONS or not isinstance(event, str) or not event:
        return True, {"operator": operator}
    decision = assign_event(event, config)
    details = {"operator": operator, "assigned_to": decision.assigned_to, "assignment_source": decision.source, "assignment_reason": decision.reason}
    if not decision.assigned_to:
        return True, details
    if operator == decision.assigned_to or operator in admins:
        return True, details
    return False, details


def event_assignment_map(project_dir: Path, events: list[str], assignment_file: Path | None = None) -> dict[str, dict[str, Any]]:
    config = load_assignments(project_dir, assignment_file=assignment_file)
    out: dict[str, dict[str, Any]] = {}
    for event in events:
        decision = assign_event(event, config)
        out[event] = {"assigned_to": decision.assigned_to, "assignment_source": decision.source, "assignment_reason": decision.reason}
    return out
