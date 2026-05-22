from __future__ import annotations

import json

from reanalyze.cgi_mailbox import append_command, drain_commands, install_cgi, resolve_spool_dir
from reanalyze.static_mailbox_manager import publish_control_page


def test_append_and_drain_mailbox_commands(tmp_path):
    spool = tmp_path / "spool"
    first = append_command(spool, "submit_event", event="S240413p")
    second = append_command(spool, "refresh")

    drained = drain_commands(spool)

    assert [item["id"] for item in drained] == [first["id"], second["id"]]
    assert drained[0]["cgi_host"]
    assert (spool / "commands.jsonl").read_text() == ""
    assert list((spool / "drained").glob("commands-*.jsonl"))


def test_append_command_rejects_missing_event(tmp_path):
    try:
        append_command(tmp_path / "spool", "submit_event")
    except ValueError as exc:
        assert "requires an event" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("missing event should be rejected")


def test_resolve_spool_dir_uses_runtime_host_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr("reanalyze.cgi_mailbox.cgi_host", lambda: "jobs5.ldas.cit")

    resolved = resolve_spool_dir(spool_root=tmp_path, mailbox_name="purohit-vaishak-rean5")

    assert resolved == tmp_path / "purohit-vaishak-rean5-jobs5.ldas.cit"


def test_explicit_spool_dir_overrides_host_aware_resolution(tmp_path, monkeypatch):
    monkeypatch.setattr("reanalyze.cgi_mailbox.cgi_host", lambda: "jobs5.ldas.cit")

    resolved = resolve_spool_dir(tmp_path / "explicit", spool_root=tmp_path, mailbox_name="ignored")

    assert resolved == (tmp_path / "explicit").resolve()


def test_install_cgi_mailbox_writes_executable_script_with_explicit_spool(tmp_path):
    cgi_path = tmp_path / "public_html" / "cgi-bin" / "purohit_mailbox.cgi"
    spool = tmp_path / "spool"
    repo_root = tmp_path / "repo"

    installed = install_cgi(cgi_path, spool, python_executable="python3", repo_root=repo_root)

    text = installed.read_text()
    assert "reanalyze.cgi_mailbox" in text
    assert str(spool.resolve()) in text
    assert str(repo_root.resolve()) in text
    assert installed.stat().st_mode & 0o111


def test_install_cgi_mailbox_defaults_to_host_aware_spool(tmp_path):
    cgi_path = tmp_path / "public_html" / "cgi-bin" / "purohit_mailbox.cgi"

    installed = install_cgi(
        cgi_path,
        spool_root=tmp_path / "var-tmp",
        mailbox_name="purohit-vaishak-rean5",
        python_executable="python3",
    )

    text = installed.read_text()
    assert "spool_dir=None" in text
    assert str((tmp_path / "var-tmp").resolve()) in text
    assert "purohit-vaishak-rean5" in text
    assert "include_host=True" in text


def test_publish_control_page_writes_mailbox_config(tmp_path):
    webdir = tmp_path / "public_html" / "monitor"

    publish_control_page(webdir, "https://example.invalid/cgi-bin/purohit_mailbox.cgi")

    assert "sendCommand" in (webdir / "commands.html").read_text()
    status = json.loads((webdir / "mailbox_status.json").read_text())
    assert status["mailbox_url"] == "https://example.invalid/cgi-bin/purohit_mailbox.cgi"
