from __future__ import annotations

import json
from pathlib import Path

from reanalyze.cgi_command import append_command, install_cgi
from reanalyze.static_monitor import publish_once


def test_append_command_creates_command_queue(tmp_path):
    command_file = tmp_path / "control" / "commands.json"

    command = append_command(command_file, "submit_event", event="S240413p", reason="test")

    payload = json.loads(command_file.read_text())
    assert payload["commands"] == [command]
    assert command["action"] == "submit_event"
    assert command["event"] == "S240413p"
    assert command["reason"] == "test"
    assert command["source"] == "cgi"


def test_append_command_rejects_missing_event_for_event_actions(tmp_path):
    command_file = tmp_path / "commands.json"

    try:
        append_command(command_file, "submit_event")
    except ValueError as exc:
        assert "requires an event" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("append_command should have rejected missing event")


def test_append_command_accepts_refresh_without_event(tmp_path):
    command_file = tmp_path / "commands.json"

    append_command(command_file, "refresh")

    payload = json.loads(command_file.read_text())
    assert payload["commands"][0]["action"] == "refresh"
    assert "event" not in payload["commands"][0]


def test_install_cgi_writes_executable_script(tmp_path):
    command_file = tmp_path / "project" / "control" / "commands.json"
    token_file = tmp_path / "project" / "control" / "token.txt"
    cgi_path = tmp_path / "public_html" / "cgi-bin" / "purohit_command.cgi"

    installed = install_cgi(cgi_path, command_file, token_file=token_file, python_executable="python3")

    text = installed.read_text()
    assert "reanalyze.cgi_command" in text
    assert str(command_file.resolve()) in text
    assert str(token_file.resolve()) in text
    assert installed.stat().st_mode & 0o111


def test_publish_once_includes_command_url(tmp_path):
    project_dir = tmp_path / "project"
    (project_dir / "working" / "S240413p").mkdir(parents=True)
    webdir = tmp_path / "public_html" / "monitor"

    payload = publish_once(
        project_dir,
        webdir,
        command_file=project_dir / "control" / "commands.json",
        command_url="/~vaishak.prasad/cgi-bin/purohit_command.cgi",
    )

    assert payload["command_url"] == "/~vaishak.prasad/cgi-bin/purohit_command.cgi"
    assert "sendCommand" in (webdir / "index.html").read_text()
