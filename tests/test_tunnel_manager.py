from __future__ import annotations

from pathlib import Path

from reanalyze.tunnel_manager import append_command, drain_queue, list_dir, normalize_rel_path, read_file, within_root


def test_tunnel_queue_append_and_drain(tmp_path):
    project = tmp_path / "project"
    first = append_command(project, {"action": "submit_event", "event": "S240413p"})
    second = append_command(project, {"action": "refresh"})

    drained = drain_queue(project)

    assert [item["id"] for item in drained] == [first["id"], second["id"]]
    assert drained[0]["source"] == "tunnel-manager"
    assert (project / "control" / "tunnel_commands.jsonl").read_text() == ""


def test_normalize_rel_path_blocks_absolute_escape():
    assert normalize_rel_path("/../../etc/passwd") == Path("etc/passwd")
    assert normalize_rel_path("a/../b") == Path("b")


def test_within_root_rejects_escape(tmp_path):
    root = tmp_path / "root"
    root.mkdir()

    try:
        within_root(root, Path("../outside"))
    except PermissionError as exc:
        assert "escapes" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("path escape should be rejected")


def test_list_and_read_file(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "sub").mkdir()
    (root / "sub" / "run.err").write_text("hello\n")

    listing = list_dir(root, Path("sub"))
    assert listing["ok"] is True
    assert listing["entries"][0]["name"] == "run.err"

    payload = read_file(root, Path("sub/run.err"), max_bytes=100)
    assert payload["content"] == "hello\n"
    assert payload["truncated"] is False
