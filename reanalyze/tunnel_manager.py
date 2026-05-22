"""Localhost tunnel manager for Purohit static web controls.

This manager runs on the submit/login host and exposes a small token-protected
HTTP API on localhost. Users can forward it through SSH, e.g.

    ssh -N -L 8766:127.0.0.1:8766 citlogin5

A static page under the public webdir can then POST commands to
http://127.0.0.1:8766/api/command and browse whitelisted project files through
the same tunnel. The command execution, audit, status publishing, and health
machinery are shared with the existing static managers.
"""

from __future__ import annotations

import argparse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import posixpath
import secrets
import tempfile
import threading
import time
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from reanalyze.manager_health import build_health_payload, publish_health_files, sanitize_command_result
from reanalyze.static_manager import append_audit, process_command
from reanalyze.static_monitor import publish_once

QUEUE_FILENAME = "tunnel_commands.jsonl"

TUNNEL_CONFIG_FILENAME = "tunnel_config.json"

TUNNEL_APP_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Purohit tunnel controls</title>
  <style>
    :root { color-scheme: light dark; }
    body { font-family: system-ui, -apple-system, BlinkMacSystemFont, sans-serif; margin: 2rem; line-height: 1.35; }
    nav a { margin-right: 1rem; }
    .card { border: 1px solid rgba(128,128,128,0.25); border-radius: 12px; padding: 1rem; margin: 1rem 0; }
    .warn { border-color: #b91c1c; background: rgba(185,28,28,0.08); }
    table { width: 100%; border-collapse: collapse; margin-top: 0.5rem; }
    th, td { text-align: left; border-bottom: 1px solid rgba(128,128,128,0.25); padding: 0.55rem; font-size: 0.9rem; vertical-align: top; }
    code, input { padding: 0.15rem 0.35rem; border-radius: 4px; background: rgba(128,128,128,0.15); }
    input { min-width: 24rem; border: 1px solid rgba(128,128,128,0.35); }
    .btn { margin: 0.08rem; border: 1px solid rgba(128,128,128,0.35); border-radius: 6px; padding: 0.32rem 0.55rem; cursor: pointer; }
    .btn-primary { background: rgba(37,99,235,0.14); border-color: rgba(37,99,235,0.35); }
    .btn-danger { background: rgba(185,28,28,0.12); border-color: rgba(185,28,28,0.35); }
    .btn-pending { background: rgba(146,64,14,0.18); border-color: rgba(146,64,14,0.55); }
    .btn-success { background: rgba(4,120,87,0.20); border-color: rgba(4,120,87,0.70); color: #047857; font-weight: 700; }
    .btn-error { background: rgba(185,28,28,0.18); border-color: rgba(185,28,28,0.70); color: #b91c1c; font-weight: 700; }
    .status { margin-top: 0.75rem; padding: 0.55rem 0.7rem; border-radius: 8px; border: 1px solid rgba(128,128,128,0.25); }
    .status-ok { border-color: rgba(4,120,87,0.55); background: rgba(4,120,87,0.10); }
    .status-error { border-color: rgba(185,28,28,0.55); background: rgba(185,28,28,0.10); }
    .status-pending { border-color: rgba(146,64,14,0.45); background: rgba(146,64,14,0.10); }
    .muted { opacity: 0.72; }
    .small { font-size: 0.82rem; }
  </style>
</head>
<body>
  <h1>Purohit tunnel controls</h1>
  <nav><a href="index.html">Monitor</a><a href="tunnel.html">Commands</a><a href="files.html">Files</a><a href="health.html">Health</a></nav>
  <p class="muted">Requires an SSH tunnel, for example <code>ssh -N -L 8766:127.0.0.1:8766 citlogin5</code>.</p>
  <div class="card">
    <div><strong>Endpoint:</strong> <input id="endpoint" value="http://127.0.0.1:8766"><button class="btn" onclick="saveEndpoint()">Save endpoint</button></div>
    <div><strong>Token:</strong> <input id="token" type="password"><button class="btn btn-primary" onclick="saveToken(this)">Save token</button></div>
    <div id="result" class="status small">No tunnel command queued yet.</div>
  </div>
  <div class="card">
    <table><thead><tr><th>Event</th><th>Status</th><th>Cluster ID</th><th>Controls</th></tr></thead><tbody id="jobs"></tbody></table>
  </div>
<script>
const ORIGINAL_LABEL = "data-original-label";
function fmt(x) { return x === null || x === undefined || x === "" ? "—" : x; }
function setStatus(message, kind="") { const r = document.getElementById("result"); r.className = `status ${kind ? `status-${kind}` : ""} small`; r.textContent = message; }
function endpoint() { return (document.getElementById("endpoint").value || "http://127.0.0.1:8766").replace(/\/$/, ""); }
function token() { return localStorage.getItem("purohit_tunnel_token") || document.getElementById("token").value || ""; }
function saveEndpoint() { localStorage.setItem("purohit_tunnel_endpoint", endpoint()); setStatus("Endpoint saved in this browser.", "ok"); }
function saveToken(button) { localStorage.setItem("purohit_tunnel_token", document.getElementById("token").value || ""); setButtonState(button, "success", "Saved ✓"); setStatus("Token saved in this browser.", "ok"); restoreButton(button); }
function setButtonState(button, state, label) { if (!button) return; if (!button.hasAttribute(ORIGINAL_LABEL)) button.setAttribute(ORIGINAL_LABEL, button.textContent); button.classList.remove("btn-pending", "btn-success", "btn-error"); if (state) button.classList.add(`btn-${state}`); if (label) button.textContent = label; button.disabled = state === "pending"; }
function restoreButton(button, delay=1800) { if (!button) return; const original = button.getAttribute(ORIGINAL_LABEL) || button.textContent; setTimeout(() => { button.classList.remove("btn-pending", "btn-success", "btn-error"); button.textContent = original; button.disabled = false; }, delay); }
function controls(event) { return `<button class="btn btn-primary" onclick="sendCommand(this,'submit_event','${event}')">Submit</button><button class="btn" onclick="sendCommand(this,'hold_event','${event}')">Hold</button><button class="btn" onclick="sendCommand(this,'release_event','${event}')">Release</button><button class="btn btn-danger" onclick="sendCommand(this,'remove_event','${event}')">Remove</button>`; }
async function sendCommand(button, action, event) {
  if (action === "remove_event" && !confirm(`Remove ${event}?`)) return;
  setButtonState(button, "pending", "Queueing…"); setStatus(`Queueing ${action} for ${event}...`, "pending");
  try {
    const response = await fetch(`${endpoint()}/api/command`, {method: "POST", headers: {"Content-Type": "application/json", "X-Purohit-Token": token()}, body: JSON.stringify({action, event})});
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.error || `HTTP ${response.status}`);
    setButtonState(button, "success", "Queued ✓");
    setStatus(`Queued ${action} for ${event}. Command ID: ${data.command?.id || "unknown"}.`, "ok");
    restoreButton(button, 2200);
  } catch (err) { setButtonState(button, "error", "Failed ✗"); setStatus(`Command failed: ${err}`, "error"); restoreButton(button, 3500); }
}
async function pingTunnel() {
  try { const r = await fetch(`${endpoint()}/api/health`, {headers: {"X-Purohit-Token": token()}}); return r.ok; } catch { return false; }
}
async function refresh() {
  const savedEndpoint = localStorage.getItem("purohit_tunnel_endpoint"); if (savedEndpoint) document.getElementById("endpoint").value = savedEndpoint;
  const savedToken = localStorage.getItem("purohit_tunnel_token"); if (savedToken && !document.getElementById("token").value) document.getElementById("token").value = savedToken;
  const ok = await pingTunnel(); if (!ok) setStatus("Tunnel endpoint is not reachable yet. Start ssh -N -L 8766:127.0.0.1:8766 citlogin5 and the tunnel manager.", "error");
  const statusResponse = await fetch(`status.json?ts=${Date.now()}`, {cache: "no-store"});
  const status = await statusResponse.json();
  document.getElementById("jobs").innerHTML = (status.jobs || []).map(job => `<tr><td>${fmt(job.event)}</td><td>${fmt(job.status)}</td><td>${fmt(job.jobid)}</td><td>${controls(job.event)}</td></tr>`).join("") || `<tr><td colspan="4">No jobs found.</td></tr>`;
}
refresh().catch(err => setStatus(`refresh error: ${err}`, "error"));
setInterval(() => refresh().catch(console.error), 30000);
</script>
</body>
</html>
"""

FILES_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Purohit tunnel file browser</title>
  <style>
    :root { color-scheme: light dark; }
    body { font-family: system-ui, -apple-system, BlinkMacSystemFont, sans-serif; margin: 2rem; line-height: 1.35; }
    nav a { margin-right: 1rem; }
    .card { border: 1px solid rgba(128,128,128,0.25); border-radius: 12px; padding: 1rem; margin: 1rem 0; }
    table { width: 100%; border-collapse: collapse; margin-top: 0.5rem; }
    th, td { text-align: left; border-bottom: 1px solid rgba(128,128,128,0.25); padding: 0.55rem; font-size: 0.9rem; }
    code, input, select { padding: 0.15rem 0.35rem; border-radius: 4px; background: rgba(128,128,128,0.15); }
    input { min-width: 22rem; border: 1px solid rgba(128,128,128,0.35); }
    button { margin: 0.08rem; border: 1px solid rgba(128,128,128,0.35); border-radius: 6px; padding: 0.32rem 0.55rem; cursor: pointer; }
    pre { padding: 0.75rem; overflow-x: auto; white-space: pre-wrap; background: rgba(128,128,128,0.10); border-radius: 8px; max-height: 70vh; }
    .muted { opacity: 0.72; }
  </style>
</head>
<body>
  <h1>Purohit tunnel file browser</h1>
  <nav><a href="index.html">Monitor</a><a href="tunnel.html">Commands</a><a href="files.html">Files</a><a href="health.html">Health</a></nav>
  <p class="muted">Read-only browser through the SSH tunnel. Paths are constrained to manager-configured roots.</p>
  <div class="card">
    <div><strong>Endpoint:</strong> <input id="endpoint" value="http://127.0.0.1:8766"><button onclick="saveEndpoint()">Save</button></div>
    <div><strong>Token:</strong> <input id="token" type="password"><button onclick="saveToken()">Save token</button></div>
    <div><strong>Root:</strong> <select id="root" onchange="loadDir('')"></select> <strong>Path:</strong> <code id="path"></code></div>
    <div id="message" class="muted"></div>
  </div>
  <div class="card"><table><thead><tr><th>Name</th><th>Type</th><th>Size</th><th>Modified</th><th>Action</th></tr></thead><tbody id="entries"></tbody></table></div>
  <div id="viewer" class="card" style="display:none"><h2 id="viewer-title"></h2><pre id="viewer-content"></pre></div>
<script>
function endpoint() { return (document.getElementById("endpoint").value || "http://127.0.0.1:8766").replace(/\/$/, ""); }
function token() { return localStorage.getItem("purohit_tunnel_token") || document.getElementById("token").value || ""; }
function saveEndpoint() { localStorage.setItem("purohit_tunnel_endpoint", endpoint()); }
function saveToken() { localStorage.setItem("purohit_tunnel_token", document.getElementById("token").value || ""); }
function fmtSize(n) { if (n === null || n === undefined) return "—"; if (n < 1024) return `${n} B`; if (n < 1024*1024) return `${(n/1024).toFixed(1)} KiB`; return `${(n/1024/1024).toFixed(1)} MiB`; }
async function api(path) { const r = await fetch(`${endpoint()}${path}`, {headers: {"X-Purohit-Token": token()}}); const d = await r.json(); if (!r.ok || !d.ok) throw new Error(d.error || `HTTP ${r.status}`); return d; }
async function init() {
  const savedEndpoint = localStorage.getItem("purohit_tunnel_endpoint"); if (savedEndpoint) document.getElementById("endpoint").value = savedEndpoint;
  const savedToken = localStorage.getItem("purohit_tunnel_token"); if (savedToken) document.getElementById("token").value = savedToken;
  const d = await api("/api/files/roots");
  document.getElementById("root").innerHTML = d.roots.map(r => `<option value="${r.id}">${r.label}</option>`).join("");
  await loadDir("");
}
async function loadDir(path) {
  try {
    document.getElementById("viewer").style.display = "none";
    const root = document.getElementById("root").value;
    const d = await api(`/api/files/list?root=${encodeURIComponent(root)}&path=${encodeURIComponent(path)}`);
    document.getElementById("path").textContent = d.path || "/";
    const up = d.path ? `<tr><td><a href="#" onclick="loadDir('${d.parent_path || ""}'); return false;">..</a></td><td>dir</td><td>—</td><td>—</td><td></td></tr>` : "";
    const rows = d.entries.map(e => `<tr><td>${e.type === "dir" ? `<a href="#" onclick="loadDir('${e.path}'); return false;">${e.name}/</a>` : e.name}</td><td>${e.type}</td><td>${fmtSize(e.size)}</td><td>${e.mtime ? new Date(e.mtime * 1000).toLocaleString() : "—"}</td><td>${e.type === "file" ? `<button onclick="viewFile('${e.path}')">View</button> <a href="${endpoint()}/api/files/download?root=${encodeURIComponent(root)}&path=${encodeURIComponent(e.path)}&token=${encodeURIComponent(token())}">Download</a>` : ""}</td></tr>`).join("");
    document.getElementById("entries").innerHTML = up + rows;
    document.getElementById("message").textContent = `${d.entries.length} entries`;
  } catch (err) { document.getElementById("message").textContent = `error: ${err}`; }
}
async function viewFile(path) {
  try {
    const root = document.getElementById("root").value;
    const d = await api(`/api/files/read?root=${encodeURIComponent(root)}&path=${encodeURIComponent(path)}&max_bytes=200000`);
    document.getElementById("viewer").style.display = "block";
    document.getElementById("viewer-title").textContent = path;
    document.getElementById("viewer-content").textContent = d.content;
  } catch (err) { document.getElementById("message").textContent = `error: ${err}`; }
}
init().catch(err => { document.getElementById("message").textContent = `error: ${err}`; });
</script>
</body>
</html>
"""


def atomic_write_text(path: Path, text: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False) as handle:
        handle.write(text)
        tmp = Path(handle.name)
    tmp.replace(path)
    try:
        path.chmod(mode)
    except OSError:
        pass


def atomic_write_json(path: Path, data: Any, mode: int = 0o644) -> None:
    atomic_write_text(path, json.dumps(data, indent=2, sort_keys=True, allow_nan=False) + "\n", mode=mode)


def read_token(token_file: Path | None) -> str | None:
    if token_file is None or not token_file.is_file():
        return None
    token = token_file.read_text().strip()
    return token or None


def token_valid(supplied: str | None, expected: str | None) -> bool:
    if expected is None:
        return True
    return secrets.compare_digest(str(supplied or ""), expected)


def queue_path(project_dir: Path) -> Path:
    return project_dir / "control" / QUEUE_FILENAME


def append_command(project_dir: Path, command: dict[str, Any]) -> dict[str, Any]:
    action = str(command.get("action") or "")
    event = command.get("event")
    queued = {
        "id": f"{int(time.time() * 1000)}-{secrets.token_hex(6)}",
        "action": action,
        "created_at": time.time(),
        "source": "tunnel-manager",
    }
    if event:
        queued["event"] = str(event)
    if command.get("reason"):
        queued["reason"] = str(command["reason"])
    path = queue_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        handle.write(json.dumps(queued, sort_keys=True) + "\n")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return queued


def drain_queue(project_dir: Path) -> list[dict[str, Any]]:
    path = queue_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("")
    with path.open("r+") as handle:
        lines = handle.readlines()
        handle.seek(0)
        handle.truncate()
    commands = []
    for line in lines:
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            item = {"action": "invalid", "error": f"invalid JSON line: {exc}", "raw": line}
        if isinstance(item, dict):
            commands.append(item)
    return commands


def normalize_rel_path(raw: str | None) -> Path:
    raw = unquote(raw or "")
    raw = posixpath.normpath("/" + raw).lstrip("/")
    return Path(raw) if raw not in ("", ".") else Path()


def within_root(root: Path, rel: Path) -> Path:
    root_resolved = root.expanduser().resolve()
    target = (root_resolved / rel).resolve()
    if target != root_resolved and root_resolved not in target.parents:
        raise PermissionError("path escapes configured root")
    return target


def list_dir(root: Path, rel: Path) -> dict[str, Any]:
    target = within_root(root, rel)
    if not target.is_dir():
        raise NotADirectoryError(str(rel))
    entries = []
    for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        try:
            stat = child.stat()
        except OSError:
            continue
        child_rel = child.relative_to(root.expanduser().resolve()).as_posix()
        entries.append({"name": child.name, "path": child_rel, "type": "dir" if child.is_dir() else "file", "size": None if child.is_dir() else stat.st_size, "mtime": stat.st_mtime})
    parent = "" if rel == Path() else rel.parent.as_posix()
    return {"ok": True, "path": rel.as_posix() if rel != Path() else "", "parent_path": "" if parent == "." else parent, "entries": entries}


def read_file(root: Path, rel: Path, max_bytes: int) -> dict[str, Any]:
    target = within_root(root, rel)
    if not target.is_file():
        raise FileNotFoundError(str(rel))
    data = target.read_bytes()[:max_bytes]
    return {"ok": True, "path": rel.as_posix(), "truncated": target.stat().st_size > len(data), "content": data.decode("utf-8", errors="replace")}


class TunnelState:
    def __init__(self, project_dir: Path, webdir: Path, token_file: Path | None, file_roots: dict[str, Path]) -> None:
        self.project_dir = project_dir.expanduser().resolve()
        self.webdir = webdir.expanduser().resolve()
        self.token_file = token_file
        self.file_roots = file_roots
        self.lock = threading.Lock()
        self.manager_started_at = time.time()
        self.last_cycle_at: float | None = None
        self.last_cycle_duration_s: float | None = None
        self.last_error: str | None = None
        self.last_plot_publish: float | None = None
        self.recent_command_results: list[dict[str, Any]] = []

    @property
    def token(self) -> str | None:
        return read_token(self.token_file)


class Handler(BaseHTTPRequestHandler):
    server_version = "PurohitTunnel/0.1"

    @property
    def state(self) -> TunnelState:
        return self.server.state  # type: ignore[attr-defined]

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Purohit-Token")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _auth_ok(self, query: dict[str, list[str]]) -> bool:
        supplied = self.headers.get("X-Purohit-Token") or (query.get("token", [None])[-1])
        return token_valid(supplied, self.state.token)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(HTTPStatus.NO_CONTENT)
        self._cors()
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if not self._auth_ok(query):
            self._send_json({"ok": False, "error": "unauthorized"}, HTTPStatus.UNAUTHORIZED)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            if parsed.path == "/api/command":
                command = append_command(self.state.project_dir, payload if isinstance(payload, dict) else {})
                self._send_json({"ok": True, "command": command})
                return
            self._send_json({"ok": False, "error": "unknown endpoint"}, HTTPStatus.NOT_FOUND)
        except Exception as exc:  # noqa: BLE001
            self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if not self._auth_ok(query):
            self._send_json({"ok": False, "error": "unauthorized"}, HTTPStatus.UNAUTHORIZED)
            return
        try:
            if parsed.path == "/api/health":
                self._send_json({"ok": True, "project_dir": str(self.state.project_dir), "queue_file": str(queue_path(self.state.project_dir))})
            elif parsed.path == "/api/files/roots":
                self._send_json({"ok": True, "roots": [{"id": key, "label": f"{key}: {path}"} for key, path in self.state.file_roots.items()]})
            elif parsed.path == "/api/files/list":
                root = self._root_from_query(query)
                self._send_json(list_dir(root, normalize_rel_path(query.get("path", [""])[-1])))
            elif parsed.path == "/api/files/read":
                root = self._root_from_query(query)
                max_bytes = int(query.get("max_bytes", ["200000"])[-1])
                self._send_json(read_file(root, normalize_rel_path(query.get("path", [""])[-1]), max_bytes=max_bytes))
            elif parsed.path == "/api/files/download":
                self._send_file(self._root_from_query(query), normalize_rel_path(query.get("path", [""])[-1]))
            else:
                self._send_json({"ok": False, "error": "unknown endpoint"}, HTTPStatus.NOT_FOUND)
        except Exception as exc:  # noqa: BLE001
            self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def _root_from_query(self, query: dict[str, list[str]]) -> Path:
        root_id = query.get("root", [next(iter(self.state.file_roots))])[-1]
        if root_id not in self.state.file_roots:
            raise ValueError(f"unknown file root {root_id!r}")
        return self.state.file_roots[root_id]

    def _send_file(self, root: Path, rel: Path) -> None:
        target = within_root(root, rel)
        if not target.is_file():
            raise FileNotFoundError(str(rel))
        data = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self._cors()
        self.send_header("Content-Type", mimetypes.guess_type(str(target))[0] or "application/octet-stream")
        self.send_header("Content-Disposition", f'attachment; filename="{target.name}"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def publish_tunnel_pages(webdir: Path, endpoint_url: str, file_roots: dict[str, Path]) -> None:
    webdir = webdir.expanduser().resolve()
    atomic_write_text(webdir / "tunnel.html", TUNNEL_APP_HTML)
    atomic_write_text(webdir / "files.html", FILES_HTML)
    atomic_write_json(webdir / TUNNEL_CONFIG_FILENAME, {"endpoint_url": endpoint_url, "file_roots": {key: str(path) for key, path in file_roots.items()}, "generated_at": time.time()})


def process_queued_commands(state: TunnelState, tail: int) -> list[dict[str, Any]]:
    commands = drain_queue(state.project_dir)
    results = []
    for command in commands:
        result = process_command(state.project_dir, command) if command.get("action") != "invalid" else {"ok": False, "command": command, "message": command.get("error")}
        append_audit(state.project_dir, result)
        results.append(result)
    state.recent_command_results.extend(sanitize_command_result(result) for result in results)
    if tail > 0:
        state.recent_command_results = state.recent_command_results[-tail:]
    return results


def manager_loop(state: TunnelState, args: argparse.Namespace) -> None:
    while True:
        cycle_start = time.time()
        try:
            with state.lock:
                results = process_queued_commands(state, args.command_result_tail)
                now = time.time()
                copy_outputs = state.last_plot_publish is None or now - state.last_plot_publish >= args.plot_interval
                payload = publish_once(
                    state.project_dir,
                    state.webdir,
                    include_history=not args.no_history,
                    heartbeat_filename=args.heartbeat_filename,
                    copy_outputs=copy_outputs,
                    command_file=queue_path(state.project_dir),
                    max_artifacts_per_event=args.max_artifacts_per_event,
                )
                publish_tunnel_pages(state.webdir, f"http://{args.host}:{args.port}", state.file_roots)
                if copy_outputs:
                    state.last_plot_publish = now
                state.last_error = None
                print(f"Processed {len(results)} tunnel command(s); published {len(payload['jobs'])} jobs to {state.webdir} at {time.strftime('%Y-%m-%d %H:%M:%S')}")
        except Exception as exc:  # noqa: BLE001
            state.last_error = str(exc)
            print(f"Tunnel manager cycle failed: {exc}")
        finally:
            state.last_cycle_at = time.time()
            state.last_cycle_duration_s = state.last_cycle_at - cycle_start
            health_payload = build_health_payload(
                project_dir=state.project_dir,
                webdir=state.webdir,
                manager_started_at=state.manager_started_at,
                last_cycle_at=state.last_cycle_at,
                last_cycle_duration_s=state.last_cycle_duration_s,
                interval_s=args.interval,
                plot_interval_s=args.plot_interval,
                mailbox_metadata={"mode": "tunnel", "endpoint_url": f"http://{args.host}:{args.port}", "queue_file": str(queue_path(state.project_dir))},
                last_artifact_publish_at=state.last_plot_publish,
                command_results_count=len(state.recent_command_results),
                env_mode=args.env_mode,
                last_error=state.last_error,
            )
            publish_health_files(state.webdir, health_payload, state.recent_command_results, atomic_write_text=atomic_write_text, atomic_write_json=atomic_write_json)
        if args.once:
            return
        time.sleep(args.interval)


def parse_file_roots(project_dir: Path, webdir: Path, values: list[str] | None) -> dict[str, Path]:
    roots: dict[str, Path] = {"project": project_dir.expanduser().resolve(), "webdir": webdir.expanduser().resolve()}
    for item in values or []:
        if "=" not in item:
            raise ValueError("--file-root must be NAME=PATH")
        name, path = item.split("=", 1)
        roots[name.strip()] = Path(path).expanduser().resolve()
    return roots


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Purohit tunnel manager and localhost command/file API.")
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument("--webdir", required=True, type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--token-file", type=Path, default=None)
    parser.add_argument("--file-root", action="append", default=[], help="Additional read-only file root as NAME=PATH")
    parser.add_argument("--interval", type=int, default=10)
    parser.add_argument("--plot-interval", type=int, default=300)
    parser.add_argument("--env-mode", choices=["names", "redacted", "full"], default="redacted")
    parser.add_argument("--command-result-tail", type=int, default=100)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--no-history", action="store_true")
    parser.add_argument("--heartbeat-filename", default="heartbeat.json")
    parser.add_argument("--max-artifacts-per-event", type=int, default=40)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    project_dir = args.project_dir.expanduser().resolve()
    webdir = args.webdir.expanduser().resolve()
    state = TunnelState(project_dir, webdir, args.token_file, parse_file_roots(project_dir, webdir, args.file_root))
    queue_path(project_dir).parent.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.state = state  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"Tunnel API listening on http://{args.host}:{args.port}")
    try:
        manager_loop(state, args)
    finally:
        server.shutdown()
        thread.join(timeout=5)


if __name__ == "__main__":
    main()
