from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any


VALID_ACTIONS = {"submit_event", "hold_event", "release_event", "remove_event", "refresh"}


def read_commands(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"commands": []}
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {"commands": []}
    if isinstance(data, list):
        return {"commands": data}
    if isinstance(data, dict):
        commands = data.get("commands", [])
        return {"commands": commands if isinstance(commands, list) else []}
    return {"commands": []}


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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Append a command to the Purohit static manager command queue.")
    parser.add_argument("action", choices=sorted(VALID_ACTIONS))
    parser.add_argument("event", nargs="?", help="Event name for event-scoped actions.")
    parser.add_argument("--command-file", required=True, type=Path, help="Path to project_dir/control/commands.json or another manager command JSON file.")
    parser.add_argument("--reason", default=None, help="Optional human-readable reason recorded with the command.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.action != "refresh" and not args.event:
        raise SystemExit(f"{args.action} requires an event")
    path = args.command_file.expanduser().resolve()
    payload = read_commands(path)
    command = {"action": args.action, "created_at": time.time()}
    if args.event:
        command["event"] = args.event
    if args.reason:
        command["reason"] = args.reason
    payload.setdefault("commands", []).append(command)
    atomic_write_json(path, payload)
    print(f"Queued {args.action} for {args.event or 'manager'} in {path}")


if __name__ == "__main__":
    main()
