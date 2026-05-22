"""Health diagnostics for the static Purohit mailbox manager."""

from __future__ import annotations

import json
import os
from pathlib import Path
import platform
import shutil
import socket
import sys
import time
from typing import Any

SENSITIVE_ENV_TOKENS = (
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "CREDENTIAL",
    "COOKIE",
    "AUTH",
    "PRIVATE",
    "KEY",
)

HEALTH_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Purohit manager health</title>
  <style>
    :root { color-scheme: light dark; }
    body { font-family: system-ui, -apple-system, BlinkMacSystemFont, sans-serif; margin: 2rem; line-height: 1.35; }
    .card { border: 1px solid rgba(128,128,128,0.25); border-radius: 12px; padding: 1rem; margin: 1rem 0; }
    .warn { border-color: #b91c1c; background: rgba(185,28,28,0.08); }
    .ok { border-color: #047857; background: rgba(4,120,87,0.08); }
    table { width: 100%; border-collapse: collapse; margin-top: 0.5rem; }
    th, td { text-align: left; border-bottom: 1px solid rgba(128,128,128,0.25); padding: 0.45rem; font-size: 0.9rem; vertical-align: top; }
    code, pre { padding: 0.1rem 0.25rem; border-radius: 4px; background: rgba(128,128,128,0.15); }
    pre { padding: 0.75rem; overflow-x: auto; white-space: pre-wrap; }
    .muted { opacity: 0.72; }
    .small { font-size: 0.82rem; }
  </style>
</head>
<body>
  <h1>Purohit manager health</h1>
  <p class="muted">Diagnostics for the CGI-mailbox control chain and the static manager process.</p>
  <div id="summary" class="card">Loading...</div>
  <h2>Manager</h2><div id="manager" class="card"></div>
  <h2>Mailbox / CGI</h2><div id="mailbox" class="card"></div>
  <h2>Environment checks</h2><div id="checks" class="card"></div>
  <h2>Recent command results</h2><div id="commands" class="card"></div>
  <h2>Environment variables</h2><div id="env" class="card"></div>
<script>
function fmt(x) { return x === null || x === undefined || x === "" ? "—" : x; }
function dt(ts) { return ts ? new Date(ts * 1000).toLocaleString() : "—"; }
function yes(x) { return x ? "yes" : "no"; }
function table(rows) { return `<table><tbody>${rows.map(([k,v]) => `<tr><th>${k}</th><td>${v}</td></tr>`).join("")}</tbody></table>`; }
function objectTable(obj) { return table(Object.entries(obj || {}).map(([k,v]) => [k, typeof v === "object" ? `<pre>${JSON.stringify(v, null, 2)}</pre>` : fmt(v)])); }
async function refresh() {
  const healthResp = await fetch(`health.json?ts=${Date.now()}`, {cache: "no-store"});
  const h = await healthResp.json();
  const cmdResp = await fetch(`command_results.json?ts=${Date.now()}`, {cache: "no-store"});
  const cr = await cmdResp.json();
  const stale = h.manager?.stale;
  const lb = h.mailbox?.load_balancing_detected;
  document.getElementById("summary").className = `card ${(stale || lb || h.last_error) ? "warn" : "ok"}`;
  document.getElementById("summary").innerHTML = `<strong>Status:</strong> ${(stale || lb || h.last_error) ? "needs attention" : "healthy"}<br><span class="small muted">Generated ${dt(h.generated_at)}</span>`;
  document.getElementById("manager").innerHTML = table([
    ["host", fmt(h.manager?.host)], ["pid", fmt(h.manager?.pid)], ["started", dt(h.manager?.started_at)],
    ["last cycle", dt(h.manager?.last_cycle_at)], ["uptime seconds", fmt(Math.round(h.manager?.uptime_seconds || 0))],
    ["cycle duration seconds", fmt(h.manager?.last_cycle_duration_s)], ["interval seconds", fmt(h.manager?.interval_s)],
    ["plot interval seconds", fmt(h.manager?.plot_interval_s)], ["stale", yes(h.manager?.stale)],
    ["python", fmt(h.manager?.python_executable)], ["cwd", fmt(h.manager?.cwd)]
  ]);
  document.getElementById("mailbox").innerHTML = objectTable(h.mailbox || {});
  const checks = h.environment_checks || {};
  document.getElementById("checks").innerHTML = `<table><thead><tr><th>Check</th><th>OK</th><th>Value</th></tr></thead><tbody>${Object.entries(checks).map(([k,v]) => `<tr><td>${k}</td><td>${yes(v.ok)}</td><td>${fmt(v.value || v.error)}</td></tr>`).join("")}</tbody></table>`;
  const results = cr.results || [];
  document.getElementById("commands").innerHTML = results.length ? `<table><thead><tr><th>processed</th><th>ok</th><th>action</th><th>event</th><th>message/jobid</th><th>queued host</th></tr></thead><tbody>${results.map(r => `<tr><td>${dt(r.processed_at)}</td><td>${yes(r.ok)}</td><td>${fmt(r.action)}</td><td>${fmt(r.event)}</td><td>${fmt(r.jobid || r.message)}</td><td>${fmt(r.queued_host)}</td></tr>`).join("")}</tbody></table>` : "No commands processed yet.";
  document.getElementById("env").innerHTML = `<p class="small muted">Mode: ${fmt(h.environment?.mode)}. Sensitive-looking values are redacted unless full env mode is explicitly requested.</p>${objectTable(h.environment?.variables || {})}`;
}
refresh().catch(err => { document.getElementById("summary").textContent = `error: ${err}`; });
setInterval(() => refresh().catch(console.error), 30000);
</script>
</body>
</html>
"""


def is_sensitive_env_key(key: str) -> bool:
    upper = key.upper()
    return any(token in upper for token in SENSITIVE_ENV_TOKENS)


def collect_environment(mode: str = "redacted") -> dict[str, Any]:
    """Collect environment variables for public diagnostics.

    ``names`` publishes only variable names, ``redacted`` publishes values but
    masks sensitive-looking variables, and ``full`` publishes raw values. The
    default should remain ``redacted`` for public_html deployments.
    """

    variables: dict[str, Any] = {}
    for key in sorted(os.environ):
        value = os.environ[key]
        if mode == "names":
            variables[key] = "<set>"
        elif mode == "full":
            variables[key] = value
        else:
            variables[key] = "<redacted>" if is_sensitive_env_key(key) else value
    return {"mode": mode, "variables": variables}


def check_writable(path: Path) -> dict[str, Any]:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / f".purohit-write-test-{os.getpid()}"
        probe.write_text("ok\n")
        probe.unlink(missing_ok=True)
        return {"ok": True, "value": str(path)}
    except Exception as exc:  # noqa: BLE001 - diagnostics should record broad failures
        return {"ok": False, "value": str(path), "error": str(exc)}


def collect_environment_checks(project_dir: Path, webdir: Path) -> dict[str, dict[str, Any]]:
    checks: dict[str, dict[str, Any]] = {}
    for executable in ("bilby_pipe", "condor_q", "condor_hold", "condor_release", "condor_rm"):
        path = shutil.which(executable)
        checks[f"{executable}_on_path"] = {"ok": path is not None, "value": path}
    checks["project_dir_writable"] = check_writable(project_dir / "control")
    checks["webdir_writable"] = check_writable(webdir)
    return checks


def sanitize_command_result(result: dict[str, Any]) -> dict[str, Any]:
    command = result.get("command") if isinstance(result.get("command"), dict) else {}
    return {
        "processed_at": time.time(),
        "ok": bool(result.get("ok")),
        "action": command.get("action") or result.get("action"),
        "event": command.get("event") or result.get("event"),
        "command_id": command.get("id"),
        "queued_host": command.get("cgi_host"),
        "jobid": result.get("jobid"),
        "message": result.get("message") or result.get("error"),
    }


def build_health_payload(
    *,
    project_dir: Path,
    webdir: Path,
    manager_started_at: float,
    last_cycle_at: float | None,
    last_cycle_duration_s: float | None,
    interval_s: int,
    plot_interval_s: int,
    mailbox_metadata: dict[str, Any],
    last_artifact_publish_at: float | None,
    command_results_count: int,
    env_mode: str,
    last_error: str | None = None,
) -> dict[str, Any]:
    now = time.time()
    stale = bool(last_cycle_at and now - last_cycle_at > 2 * interval_s)
    return {
        "generated_at": now,
        "last_error": last_error,
        "manager": {
            "host": socket.getfqdn() or socket.gethostname(),
            "pid": os.getpid(),
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "python_executable": sys.executable,
            "argv": sys.argv,
            "cwd": os.getcwd(),
            "started_at": manager_started_at,
            "uptime_seconds": now - manager_started_at,
            "last_cycle_at": last_cycle_at,
            "last_cycle_duration_s": last_cycle_duration_s,
            "interval_s": interval_s,
            "plot_interval_s": plot_interval_s,
            "stale": stale,
        },
        "mailbox": mailbox_metadata,
        "artifacts": {
            "last_artifact_publish_at": last_artifact_publish_at,
            "seconds_since_artifact_publish": None if last_artifact_publish_at is None else now - last_artifact_publish_at,
        },
        "command_results": {
            "published_count": command_results_count,
        },
        "environment_checks": collect_environment_checks(project_dir, webdir),
        "environment": collect_environment(env_mode),
    }


def publish_health_files(
    webdir: Path,
    health_payload: dict[str, Any],
    command_results: list[dict[str, Any]],
    *,
    atomic_write_text,
    atomic_write_json,
) -> None:
    webdir = webdir.expanduser().resolve()
    atomic_write_text(webdir / "health.html", HEALTH_HTML)
    atomic_write_json(webdir / "health.json", health_payload)
    atomic_write_json(
        webdir / "command_results.json",
        {"generated_at": time.time(), "results": command_results},
    )
