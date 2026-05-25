"""Purohit tunnel web app with unauthenticated static pages and tokened API.

The older tunnel manager guarded every GET request with the command token.  That
is correct for ``/api/*`` endpoints, but it makes opening ``index.html`` or
``tunnel.html`` return ``{"ok": false, "error": "unauthorized"}`` before the
browser has a chance to load the UI and save a token.

This module reuses the existing tunnel manager state, API handler, manager loop,
and static-page publisher, but changes request routing so that static files from
``webdir`` are readable without a token while all API endpoints remain protected.
"""

from __future__ import annotations

from http import HTTPStatus
from http.server import ThreadingHTTPServer
import mimetypes
import posixpath
import threading
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from reanalyze.output_products import apply as apply_output_products
from reanalyze.tunnel_manager import (
    Handler,
    TunnelState,
    manager_loop,
    parse_args,
    parse_file_roots,
    queue_path,
)

LOGIN_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Purohit sign in</title>
  <style>
    :root { color-scheme: light dark; }
    body { font-family: system-ui, -apple-system, BlinkMacSystemFont, sans-serif; margin: 2rem; line-height: 1.35; }
    nav a { margin-right: 1rem; }
    .card { border: 1px solid rgba(128,128,128,0.25); border-radius: 12px; padding: 1rem; margin: 1rem 0; max-width: 820px; }
    input { min-width: 34rem; max-width: 100%; padding: 0.35rem 0.45rem; border-radius: 6px; border: 1px solid rgba(128,128,128,0.35); background: rgba(128,128,128,0.12); }
    button { margin-left: 0.35rem; border: 1px solid rgba(128,128,128,0.35); border-radius: 6px; padding: 0.35rem 0.65rem; cursor: pointer; }
    code { padding: 0.1rem 0.25rem; border-radius: 4px; background: rgba(128,128,128,0.15); }
    .muted { opacity: 0.72; }
    .ok { color: #047857; font-weight: 700; }
    .error { color: #b91c1c; font-weight: 700; }
  </style>
</head>
<body>
  <h1>Purohit sign in</h1>
  <nav><a href="index.html">Monitor</a><a href="tunnel.html">Commands</a><a href="files.html">Files</a><a href="health.html">Health</a></nav>

  <div class="card">
    <p class="muted">Static monitor pages are readable through the SSH tunnel. Commands, health checks, and file browsing use the local API and require the token printed by the cluster-side manager.</p>
    <label for="token"><strong>Token</strong></label><br>
    <input id="token" type="password" autocomplete="off" placeholder="Paste token from the remote token file">
    <button onclick="saveAndTest()">Sign in</button>
    <button onclick="clearToken()">Clear</button>
    <p id="status" class="muted">No token checked yet.</p>
  </div>

  <div class="card">
    <p><strong>After sign-in:</strong></p>
    <p><a href="index.html">Open monitor</a> · <a href="tunnel.html">Open command manager</a> · <a href="files.html">Open file browser</a> · <a href="health.html">Open health page</a></p>
    <p class="muted">For backwards compatibility, API requests may still pass <code>?token=...</code>, but the preferred browser workflow is to save the token here or in the Commands page.</p>
  </div>

<script>
const KEY = "purohit_tunnel_token";
function qs(name) { return new URLSearchParams(window.location.search).get(name); }
function endpoint() { return window.location.origin; }
function setStatus(msg, cls="muted") { const s = document.getElementById("status"); s.className = cls; s.textContent = msg; }
function load() {
  const fromQuery = qs("token");
  if (fromQuery) {
    localStorage.setItem(KEY, fromQuery);
    document.getElementById("token").value = fromQuery;
    window.history.replaceState({}, document.title, window.location.pathname);
    saveAndTest();
    return;
  }
  const saved = localStorage.getItem(KEY) || "";
  document.getElementById("token").value = saved;
  if (saved) setStatus("A token is saved in this browser. Click Sign in to verify it.", "muted");
}
async function saveAndTest() {
  const token = document.getElementById("token").value || "";
  if (!token) { setStatus("Paste a token first.", "error"); return; }
  localStorage.setItem(KEY, token);
  setStatus("Checking token...", "muted");
  try {
    const r = await fetch(`${endpoint()}/api/health`, {headers: {"X-Purohit-Token": token}});
    if (r.status === 401) throw new Error("token rejected");
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    setStatus("Signed in. Command and file APIs are authorized for this browser.", "ok");
  } catch (err) {
    setStatus(`Sign-in failed: ${err}`, "error");
  }
}
function clearToken() {
  localStorage.removeItem(KEY);
  document.getElementById("token").value = "";
  setStatus("Saved token cleared from this browser.", "muted");
}
load();
</script>
</body>
</html>
"""


def _normalise_static_path(raw_path: str) -> Path:
    path = unquote(raw_path or "/")
    if path in ("", "/"):
        return Path("index.html")
    if path in ("/login", "/login.html"):
        return Path("login.html")
    normalised = posixpath.normpath("/" + path).lstrip("/")
    return Path(normalised) if normalised not in ("", ".") else Path("index.html")


def _resolve_inside(root: Path, rel: Path) -> Path:
    root_resolved = root.expanduser().resolve()
    target = (root_resolved / rel).resolve()
    if target != root_resolved and root_resolved not in target.parents:
        raise PermissionError("path escapes webdir")
    return target


class StaticFirstHandler(Handler):
    """Serve static UI files without auth; keep the inherited tokened API."""

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            super().do_GET()
            return
        self._send_static(parsed.path, parse_qs(parsed.query))

    def _send_static(self, raw_path: str, query: dict[str, list[str]]) -> None:
        rel = _normalise_static_path(raw_path)
        if rel == Path("login.html"):
            self._send_html(LOGIN_HTML)
            return
        try:
            target = _resolve_inside(self.state.webdir, rel)
            if target.is_dir():
                target = _resolve_inside(self.state.webdir, rel / "index.html")
            if not target.is_file():
                self._send_json({"ok": False, "error": f"static file not found: {rel.as_posix()}"}, HTTPStatus.NOT_FOUND)
                return
            data = target.read_bytes()
            self.send_response(HTTPStatus.OK)
            self._cors()
            self.send_header("Content-Type", mimetypes.guess_type(str(target))[0] or "application/octet-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except Exception as exc:  # noqa: BLE001
            self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def _send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self._cors()
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    apply_output_products()
    args = parse_args()
    project_dir = args.project_dir.expanduser().resolve()
    webdir = args.webdir.expanduser().resolve()
    state = TunnelState(project_dir, webdir, args.token_file, parse_file_roots(project_dir, webdir, args.file_root))
    queue_path(project_dir).parent.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((args.host, args.port), StaticFirstHandler)
    server.state = state  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"Purohit web app listening on http://{args.host}:{args.port}")
    print(f"Static files are served from {webdir}; /api/* endpoints require the token.")
    try:
        manager_loop(state, args)
    finally:
        server.shutdown()
        thread.join(timeout=5)


if __name__ == "__main__":
    main()
