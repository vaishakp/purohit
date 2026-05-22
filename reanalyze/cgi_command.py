"""CGI command ingress for the static Purohit manager.

The CGI endpoint is intentionally narrow: it accepts only JSON/form requests for
known manager actions and appends them to the manager command JSON file. The
background static manager remains responsible for executing commands.
"""

from __future__ import annotations

import argparse
import cgi
import json
import os
from pathlib import Path
import secrets
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


def atomic_write_json(path: Path, data: Any, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False) as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")
        tmp = Path(handle.name)
    os.replace(tmp, path)
    try:
        path.chmod(mode)
    except OSError:
        pass


def append_command(command_file: Path, action: str, event: str | None = None, reason: str | None = None, source: str = "cgi") -> dict[str, Any]:
    if action not in VALID_ACTIONS:
        raise ValueError(f"unsupported action {action!r}")
    if action != "refresh" and not event:
        raise ValueError(f"{action} requires an event")
    payload = read_commands(command_file)
    command: dict[str, Any] = {"action": action, "created_at": time.time(), "source": source}
    if event:
        command["event"] = event
    if reason:
        command["reason"] = reason
    payload.setdefault("commands", []).append(command)
    atomic_write_json(command_file, payload)
    return command


def _read_body() -> dict[str, Any]:
    method = os.environ.get("REQUEST_METHOD", "GET").upper()
    content_type = os.environ.get("CONTENT_TYPE", "")
    if method == "POST" and "application/json" in content_type:
        length = int(os.environ.get("CONTENT_LENGTH") or 0)
        raw = os.read(0, length).decode("utf-8") if length else "{}"
        data = json.loads(raw or "{}")
        return data if isinstance(data, dict) else {}

    form = cgi.FieldStorage()
    return {key: form.getfirst(key) for key in form.keys()}


def _json_response(payload: dict[str, Any], status: str = "200 OK") -> None:
    print(f"Status: {status}")
    print("Content-Type: application/json")
    print("Access-Control-Allow-Origin: *")
    print("Access-Control-Allow-Headers: Content-Type, X-Purohit-Token")
    print("Access-Control-Allow-Methods: POST, OPTIONS")
    print()
    print(json.dumps(payload, indent=2, sort_keys=True))


def run_cgi(command_file: Path, token_file: Path | None = None) -> None:
    if os.environ.get("REQUEST_METHOD", "GET").upper() == "OPTIONS":
        _json_response({"ok": True})
        return

    try:
        data = _read_body()
        expected_token = None
        if token_file is not None and token_file.is_file():
            expected_token = token_file.read_text().strip()
        if expected_token:
            supplied = data.get("token") or os.environ.get("HTTP_X_PUROHIT_TOKEN")
            if not secrets.compare_digest(str(supplied or ""), expected_token):
                _json_response({"ok": False, "error": "unauthorized"}, status="401 Unauthorized")
                return
        action = str(data.get("action") or "")
        event = data.get("event")
        reason = data.get("reason")
        command = append_command(command_file.expanduser().resolve(), action, event=str(event) if event else None, reason=str(reason) if reason else None)
        _json_response({"ok": True, "queued": command, "command_file": str(command_file.expanduser().resolve())})
    except Exception as exc:  # noqa: BLE001 - CGI should return JSON errors to browser
        _json_response({"ok": False, "error": str(exc)}, status="400 Bad Request")


def cgi_script_text(command_file: Path, token_file: Path | None = None, python_executable: str | None = None) -> str:
    python_executable = python_executable or os.environ.get("PYTHON", "/usr/bin/env python3")
    if not python_executable.startswith("/"):
        shebang = f"#!/usr/bin/env {python_executable}"
    else:
        shebang = f"#!{python_executable}"
    token_line = "None" if token_file is None else repr(str(token_file.expanduser().resolve()))
    return f'''{shebang}
from pathlib import Path
from reanalyze.cgi_command import run_cgi

run_cgi(
    command_file=Path({str(command_file.expanduser().resolve())!r}),
    token_file=None if {token_line} is None else Path({token_line}),
)
'''


def install_cgi(cgi_path: Path, command_file: Path, token_file: Path | None = None, python_executable: str | None = None) -> Path:
    cgi_path = cgi_path.expanduser().resolve()
    cgi_path.parent.mkdir(parents=True, exist_ok=True)
    text = cgi_script_text(command_file, token_file=token_file, python_executable=python_executable)
    cgi_path.write_text(text)
    cgi_path.chmod(0o755)
    return cgi_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install or run the Purohit CGI command ingress.")
    parser.add_argument("--command-file", required=True, type=Path)
    parser.add_argument("--cgi-path", type=Path, help="CGI path to install, e.g. ~/public_html/cgi-bin/purohit_command.cgi")
    parser.add_argument("--token-file", type=Path, default=None)
    parser.add_argument("--python-executable", default=None)
    parser.add_argument("--run-cgi", action="store_true", help="Run as CGI instead of installing a script.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.run_cgi:
        run_cgi(args.command_file, token_file=args.token_file)
        return
    if args.cgi_path is None:
        raise SystemExit("--cgi-path is required unless --run-cgi is used")
    installed = install_cgi(args.cgi_path, args.command_file, token_file=args.token_file, python_executable=args.python_executable)
    print(f"Installed CGI command ingress at {installed}")


if __name__ == "__main__":
    main()
