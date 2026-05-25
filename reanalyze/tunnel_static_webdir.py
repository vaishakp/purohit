"""Static webdir serving shim for the Purohit tunnel manager.

The tunnel manager exposes authenticated JSON APIs under ``/api/*``.  The
monitor pages themselves are static files published into ``state.webdir`` and
should be readable through the same localhost SSH tunnel without an API token;
the pages use the token only for API calls that queue commands or browse files.
"""

from __future__ import annotations

from http import HTTPStatus
import mimetypes
import posixpath
from pathlib import Path
from urllib.parse import unquote, urlparse


def apply() -> None:
    """Patch ``reanalyze.tunnel_manager.Handler`` to serve static webdir files."""

    from reanalyze import tunnel_manager

    handler_cls = tunnel_manager.Handler
    if getattr(handler_cls, "_purohit_static_webdir_patched", False):
        return

    original_do_get = handler_cls.do_GET

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            return original_do_get(self)
        return _send_static_file(self, parsed.path)

    handler_cls.do_GET = do_GET
    handler_cls._purohit_static_webdir_patched = True


def _send_static_file(handler, raw_path: str) -> None:
    webdir = handler.state.webdir.expanduser().resolve()
    rel = _normalise_static_path(raw_path)
    target = (webdir / rel).resolve()

    if target == webdir or target.is_dir():
        target = target / "index.html"

    if webdir not in target.parents:
        handler._send_json({"ok": False, "error": "path escapes webdir"}, HTTPStatus.FORBIDDEN)
        return

    if not target.is_file():
        handler._send_json({"ok": False, "error": f"static file not found: {rel}"}, HTTPStatus.NOT_FOUND)
        return

    data = target.read_bytes()
    handler.send_response(HTTPStatus.OK)
    handler._cors()
    handler.send_header("Content-Type", mimetypes.guess_type(str(target))[0] or "application/octet-stream")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def _normalise_static_path(raw_path: str) -> Path:
    if raw_path in ("", "/"):
        return Path("index.html")
    normalised = posixpath.normpath("/" + unquote(raw_path)).lstrip("/")
    if normalised in ("", "."):
        return Path("index.html")
    return Path(normalised)
