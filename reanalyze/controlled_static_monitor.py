"""Controlled static monitor publisher with queued submit actions.

This module extends the static webdir monitor with an optional control loop. On
each refresh it first processes JSON command files from a local inbox, then
publishes static monitor files. The browser UI can show a submit button for
pending jobs when a command-ingress URL is configured.

Important: a static web page cannot itself write to a protected filesystem on the
submit host. The ``control_request_url`` is expected to be an authenticated
site-specific endpoint that accepts a JSON request and writes a command file into
``control_inbox``. The submit-side monitor process then validates and executes
that command file.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import time

from reanalyze.control_queue import process_command_queue
from reanalyze.static_monitor import atomic_write_json, atomic_write_text, build_status


CONTROLLED_INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Purohit controlled job monitor</title>
  <style>
    :root { color-scheme: light dark; }
    body { font-family: system-ui, -apple-system, BlinkMacSystemFont, sans-serif; margin: 2rem; line-height: 1.35; }
    h1, h2 { margin-bottom: 0.4rem; }
    .muted { opacity: 0.72; }
    .card { border: 1px solid rgba(128,128,128,0.25); border-radius: 12px; padding: 1rem; margin: 1rem 0; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 1rem; }
    table { width: 100%; border-collapse: collapse; margin-top: 0.5rem; }
    th, td { text-align: left; border-bottom: 1px solid rgba(128,128,128,0.25); padding: 0.55rem; font-size: 0.9rem; vertical-align: top; }
    th { position: sticky; top: 0; background: Canvas; }
    code { padding: 0.1rem 0.25rem; border-radius: 4px; background: rgba(128,128,128,0.15); }
    button { border: 1px solid rgba(128,128,128,0.4); border-radius: 8px; padding: 0.35rem 0.6rem; cursor: pointer; }
    button:disabled { opacity: 0.5; cursor: not-allowed; }
    .status-running { color: #047857; font-weight: 700; }
    .status-completed { color: #2563eb; font-weight: 700; }
    .status-held, .status-removed, .error { color: #b91c1c; font-weight: 700; }
    .status-idle, .status-submitted, .status-pending { color: #92400e; font-weight: 700; }
    .small { font-size: 0.82rem; }
    .ok { color: #047857; font-weight: 700; }
  </style>
</head>
<body>
  <h1>Purohit controlled job monitor</h1>
  <p class="muted">Static webdir monitor with optional queued submit requests. Commands are executed only by the submit-side control agent.</p>

  <div class="card">
    <div><strong>Project:</strong> <code id="project-dir">loading...</code></div>
    <div><strong>Generated:</strong> <span id="generated">loading...</span></div>
    <div><strong>Publisher host:</strong> <span id="publisher-host">loading...</span></div>
    <div><strong>Control:</strong> <span id="control-state">loading...</span></div>
    <div id="control-message" class="small muted"></div>
  </div>

  <h2>Publisher host resources</h2>
  <div id="system" class="grid"></div>

  <h2>Jobs</h2>
  <div class="card">
    <table>
      <thead>
        <tr>
          <th>Event</th>
          <th>Status</th>
          <th>Action</th>
          <th>Cluster ID</th>
          <th>CPUs</th>
          <th>Memory MB</th>
          <th>Remote host / node</th>
          <th>Runtime</th>
          <th>Notes</th>
        </tr>
      </thead>
      <tbody id="jobs"></tbody>
    </table>
  </div>

<script>
let currentData = null;

function fmt(value) {
  return value === null || value === undefined || value === "" ? "—" : value;
}

function card(label, value) {
  return `<div class="card"><div class="muted small">${label}</div><div><strong>${fmt(value)}</strong></div></div>`;
}

function statusClass(status) {
  return `status-${String(status || "unknown").toLowerCase()}`;
}

function controlEnabled(data) {
  return Boolean(data.control && data.control.enabled && data.control.request_url);
}

function setControlMessage(text, cls="muted") {
  const elem = document.getElementById("control-message");
  elem.textContent = text || "";
  elem.className = `small ${cls}`;
}

async function submitEvent(eventName) {
  if (!currentData || !controlEnabled(currentData)) {
    setControlMessage("Control is not enabled for this monitor.", "error");
    return;
  }

  const ok = window.confirm(`Submit event ${eventName}?`);
  if (!ok) return;

  const payload = {
    action: "submit_event",
    event: eventName,
    requested_at: new Date().toISOString(),
    requested_from: window.location.href
  };

  try {
    const response = await fetch(currentData.control.request_url, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload)
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    setControlMessage(`Queued submit request for ${eventName}. The monitor agent will process it on the next refresh.`, "ok");
  } catch (err) {
    setControlMessage(`Failed to queue submit request for ${eventName}: ${err}`, "error");
  }
}

function actionCell(job, data) {
  const status = String(job.status || "").toLowerCase();
  if (status !== "pending") return "—";
  if (!controlEnabled(data)) return `<span class="muted small">read-only</span>`;
  return `<button onclick="submitEvent('${String(job.event).replaceAll("'", "\\'")}')">Submit</button>`;
}

async function refresh() {
  const response = await fetch(`status.json?ts=${Date.now()}`, {cache: "no-store"});
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const data = await response.json();
  currentData = data;

  document.getElementById("project-dir").textContent = data.project_dir;
  document.getElementById("generated").textContent = new Date(data.generated_at * 1000).toLocaleString();
  document.getElementById("publisher-host").textContent = data.system.hostname || "—";
  document.getElementById("control-state").innerHTML = controlEnabled(data)
    ? `<span class="ok">enabled</span>`
    : `<span class="muted">read-only</span>`;

  const s = data.system || {};
  document.getElementById("system").innerHTML = [
    card("Host", s.hostname),
    card("Platform", s.platform),
    card("Logical CPUs", s.cpu_count_logical),
    card("Physical CPUs", s.cpu_count_physical),
    card("CPU frequency MHz", s.cpu_freq_mhz_current),
    card("Load average 1m", s.load_avg_1m),
    card("Load %", s.load_percent_1m),
    card("Memory used %", s.memory_used_percent),
  ].join("");

  const rows = (data.jobs || []).map(job => `<tr>
      <td>${fmt(job.event)}</td>
      <td class="${statusClass(job.status)}">${fmt(job.status)}</td>
      <td>${actionCell(job, data)}</td>
      <td>${fmt(job.jobid)}</td>
      <td>${fmt(job.request_cpus)}</td>
      <td>${fmt(job.request_memory_mb)}</td>
      <td>${fmt(job.remote_host)}</td>
      <td>${fmt(job.runtime)}</td>
      <td class="small">${fmt(job.note)}</td>
    </tr>`).join("");
  document.getElementById("jobs").innerHTML = rows || `<tr><td colspan="9">No jobs found.</td></tr>`;
}

refresh().catch(err => {
  document.getElementById("generated").textContent = `error: ${err}`;
});
setInterval(() => refresh().catch(console.error), 30000);
</script>
</body>
</html>
"""


def build_control_metadata(control_request_url: str | None, control_inbox: Path | None) -> dict[str, object]:
    """Build non-secret control metadata for ``status.json``."""

    return {
        "enabled": control_request_url is not None,
        "request_url": control_request_url,
        "inbox_configured": control_inbox is not None,
    }


def publish_controlled_once(
    project_dir: Path,
    webdir: Path,
    include_history: bool = True,
    heartbeat_filename: str = "heartbeat.json",
    control_request_url: str | None = None,
    control_inbox: Path | None = None,
    control_secret_file: Path | None = None,
    allow_unsigned_control: bool = False,
    max_commands: int = 10,
) -> dict[str, object]:
    """Process queued commands, then publish one controlled monitor update."""

    control_results: list[dict[str, object]] = []
    if control_inbox is not None:
        control_results = process_command_queue(
            project_dir=project_dir,
            inbox_dir=control_inbox,
            secret_file=control_secret_file,
            allow_unsigned=allow_unsigned_control,
            max_commands=max_commands,
        )

    payload = build_status(project_dir, include_history=include_history, heartbeat_filename=heartbeat_filename)
    payload["control"] = build_control_metadata(control_request_url, control_inbox)
    payload["control_results"] = control_results

    webdir = webdir.expanduser().resolve()
    webdir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(webdir / "status.json", payload)
    atomic_write_text(webdir / "index.html", CONTROLLED_INDEX_HTML)
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Publish a controlled static Purohit monitor into a webdir.")
    parser.add_argument("--project-dir", required=True, type=Path, help="Project directory containing submitted_jobs.txt and working/.")
    parser.add_argument("--webdir", required=True, type=Path, help="Output directory served by webdir/PESummary infrastructure.")
    parser.add_argument("--interval", type=int, default=300, help="Refresh interval in seconds when running continuously.")
    parser.add_argument("--once", action="store_true", help="Publish once and exit.")
    parser.add_argument("--no-history", action="store_true", help="Do not query condor_history when condor_q has no live job.")
    parser.add_argument("--heartbeat-filename", default="heartbeat.json", help="Per-event heartbeat JSON filename relative to project_dir/working/<event>/.")
    parser.add_argument("--copy-to", type=Path, default=None, help="Optional extra directory to mirror the generated static monitor files into.")
    parser.add_argument("--control-request-url", default=None, help="Authenticated endpoint that queues control commands from the browser UI.")
    parser.add_argument("--control-inbox", type=Path, default=None, help="Local directory polled for JSON control command files.")
    parser.add_argument("--control-secret-file", type=Path, default=None, help="Shared secret file for HMAC-signed command files.")
    parser.add_argument("--allow-unsigned-control", action="store_true", help="Allow unsigned local command files. Use only for a trusted local inbox.")
    parser.add_argument("--max-commands", type=int, default=10, help="Maximum queued commands to process per refresh cycle.")
    return parser.parse_args()


def mirror_webdir(source: Path, destination: Path) -> None:
    """Mirror generated controlled static files into an additional directory."""

    destination.mkdir(parents=True, exist_ok=True)
    for name in ("index.html", "status.json"):
        shutil.copy2(source / name, destination / name)


def main() -> None:
    args = parse_args()
    include_history = not args.no_history

    while True:
        payload = publish_controlled_once(
            project_dir=args.project_dir,
            webdir=args.webdir,
            include_history=include_history,
            heartbeat_filename=args.heartbeat_filename,
            control_request_url=args.control_request_url,
            control_inbox=args.control_inbox,
            control_secret_file=args.control_secret_file,
            allow_unsigned_control=args.allow_unsigned_control,
            max_commands=args.max_commands,
        )
        if args.copy_to is not None:
            mirror_webdir(args.webdir.expanduser().resolve(), args.copy_to.expanduser().resolve())
        print(
            f"Published {len(payload['jobs'])} jobs and processed "
            f"{len(payload.get('control_results', []))} commands to {args.webdir} at "
            f"{time.strftime('%Y-%m-%d %H:%M:%S')}"
        )
        if args.once:
            return
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
