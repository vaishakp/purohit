"""Read-only web monitor for bilby_pipe rerun projects.

The monitor is intended to run under the user's account on the submit or login
machine that can see the project directory and query HTCondor. It exposes a
small Flask web UI plus JSON endpoints for remote status monitoring.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import socket
import subprocess
import sys
import time
from typing import Any

import yaml

try:
    import psutil
except ImportError:  # pragma: no cover - exercised only in minimal installs
    psutil = None

try:
    from flask import Flask, jsonify, request
except ImportError as exc:  # pragma: no cover - import-time user guidance
    raise ImportError(
        "The web monitor requires Flask. Install monitor dependencies with "
        "`python -m pip install -r requirements-monitor.txt`."
    ) from exc


CONDOR_STATUS = {
    1: "idle",
    2: "running",
    3: "completed",
    4: "removed",
    5: "held",
    6: "suspended",
}


HTML_PAGE = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Purohit job monitor</title>
  <style>
    body { font-family: system-ui, sans-serif; margin: 2rem; background: #f7f7f8; color: #111827; }
    h1, h2 { margin-bottom: 0.4rem; }
    .card { background: white; border-radius: 12px; padding: 1rem; margin: 1rem 0; box-shadow: 0 1px 4px rgba(0,0,0,0.08); }
    table { width: 100%; border-collapse: collapse; background: white; }
    th, td { text-align: left; border-bottom: 1px solid #e5e7eb; padding: 0.55rem; font-size: 0.9rem; }
    th { background: #f3f4f6; }
    code { background: #f3f4f6; padding: 0.15rem 0.25rem; border-radius: 4px; }
    .status-running { color: #047857; font-weight: 700; }
    .status-held, .status-removed { color: #b91c1c; font-weight: 700; }
    .status-idle, .status-submitted { color: #92400e; font-weight: 700; }
    .muted { color: #6b7280; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 1rem; }
  </style>
</head>
<body>
  <h1>Purohit job monitor</h1>
  <p class="muted">Read-only view of local project ledger, HTCondor status, and machine resources.</p>

  <div class="card">
    <div><strong>Project:</strong> <code id="project-dir">loading...</code></div>
    <div><strong>Last update:</strong> <span id="updated">loading...</span></div>
  </div>

  <h2>Machine resources</h2>
  <div class="grid" id="system"></div>

  <h2>Jobs</h2>
  <div class="card">
    <table>
      <thead>
        <tr>
          <th>Event</th>
          <th>Status</th>
          <th>Cluster ID</th>
          <th>CPUs</th>
          <th>Memory MB</th>
          <th>Node / remote host</th>
          <th>Run time</th>
          <th>Notes</th>
        </tr>
      </thead>
      <tbody id="jobs"></tbody>
    </table>
  </div>

<script>
const params = new URLSearchParams(window.location.search);
const token = params.get("token") || "";
const headers = token ? {"X-Auth-Token": token} : {};

function fmt(value) {
  return value === null || value === undefined || value === "" ? "—" : value;
}

function metricCard(label, value) {
  return `<div class="card"><div class="muted">${label}</div><div><strong>${fmt(value)}</strong></div></div>`;
}

function render(data) {
  document.getElementById("project-dir").textContent = data.project_dir;
  document.getElementById("updated").textContent = new Date(data.generated_at * 1000).toLocaleString();

  const sys = data.system;
  document.getElementById("system").innerHTML = [
    metricCard("Host", sys.hostname),
    metricCard("Platform", sys.platform),
    metricCard("Logical CPUs", sys.cpu_count_logical),
    metricCard("Physical CPUs", sys.cpu_count_physical),
    metricCard("CPU freq MHz", sys.cpu_freq_mhz_current),
    metricCard("Load average", sys.load_avg_1m),
    metricCard("Load %", sys.load_percent_1m),
    metricCard("Memory used %", sys.memory_used_percent),
  ].join("");

  const rows = data.jobs.map(job => {
    const cls = `status-${(job.status || "unknown").toLowerCase()}`;
    return `<tr>
      <td>${fmt(job.event)}</td>
      <td class="${cls}">${fmt(job.status)}</td>
      <td>${fmt(job.jobid)}</td>
      <td>${fmt(job.request_cpus)}</td>
      <td>${fmt(job.request_memory_mb)}</td>
      <td>${fmt(job.remote_host)}</td>
      <td>${fmt(job.runtime)}</td>
      <td>${fmt(job.note)}</td>
    </tr>`;
  }).join("");
  document.getElementById("jobs").innerHTML = rows || `<tr><td colspan="8">No jobs found.</td></tr>`;
}

async function refresh() {
  try {
    const response = await fetch("/api/summary", {headers});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    render(await response.json());
  } catch (err) {
    document.getElementById("updated").textContent = `error: ${err}`;
  }
}

refresh();
setInterval(refresh, 15000);
</script>
</body>
</html>
"""


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    with path.open("r") as handle:
        return yaml.safe_load(handle) or {}


def _read_submitted_jobs(project_dir: Path) -> list[str]:
    ledger = project_dir / "submitted_jobs.txt"
    if not ledger.is_file():
        return []
    return [line.strip() for line in ledger.read_text().splitlines() if line.strip()]


def _discover_events(project_dir: Path) -> list[str]:
    working = project_dir / "working"
    if not working.is_dir():
        return []
    return sorted(path.name for path in working.iterdir() if path.is_dir())


def _condor_q_json(jobid: str | int | None) -> dict[str, Any] | None:
    if jobid in (None, ""):
        return None

    command = [
        "condor_q",
        str(jobid),
        "-json",
        "-attributes",
        "ClusterId,ProcId,JobStatus,RequestCpus,RequestMemory,RemoteHost,RemoteWallClockTime,JobCurrentStartDate,EnteredCurrentStatus,HoldReason",
    ]

    try:
        out = subprocess.run(command, check=True, capture_output=True, text=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None

    try:
        ads = json.loads(out.stdout)
    except json.JSONDecodeError:
        return None

    if not ads:
        return None
    return ads[0]


def _format_runtime(seconds: Any) -> str | None:
    if seconds is None:
        return None
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return None
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def collect_system_info() -> dict[str, Any]:
    """Collect basic resource information for the monitor host."""

    cpu_count = os.cpu_count() or 1
    load_avg = os.getloadavg() if hasattr(os, "getloadavg") else (None, None, None)
    load_percent = None
    if load_avg[0] is not None:
        load_percent = round(100.0 * load_avg[0] / cpu_count, 1)

    info: dict[str, Any] = {
        "hostname": socket.gethostname(),
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "cpu_count_logical": cpu_count,
        "cpu_count_physical": None,
        "cpu_freq_mhz_current": None,
        "cpu_freq_mhz_min": None,
        "cpu_freq_mhz_max": None,
        "load_avg_1m": None if load_avg[0] is None else round(load_avg[0], 3),
        "load_avg_5m": None if load_avg[1] is None else round(load_avg[1], 3),
        "load_avg_15m": None if load_avg[2] is None else round(load_avg[2], 3),
        "load_percent_1m": load_percent,
        "memory_total_gb": None,
        "memory_available_gb": None,
        "memory_used_percent": None,
    }

    if psutil is None:
        return info

    info["cpu_count_logical"] = psutil.cpu_count(logical=True)
    info["cpu_count_physical"] = psutil.cpu_count(logical=False)

    freq = psutil.cpu_freq()
    if freq is not None:
        info["cpu_freq_mhz_current"] = round(freq.current, 1)
        info["cpu_freq_mhz_min"] = round(freq.min, 1)
        info["cpu_freq_mhz_max"] = round(freq.max, 1)

    memory = psutil.virtual_memory()
    info["memory_total_gb"] = round(memory.total / 1024**3, 2)
    info["memory_available_gb"] = round(memory.available / 1024**3, 2)
    info["memory_used_percent"] = memory.percent

    return info


def collect_jobs(project_dir: Path) -> list[dict[str, Any]]:
    """Collect job ledger, persisted status, and best-effort HTCondor details."""

    submitted = set(_read_submitted_jobs(project_dir))
    events = sorted(set(_discover_events(project_dir)) | submitted)
    rows: list[dict[str, Any]] = []

    for event in events:
        event_dir = project_dir / "working" / event
        status_info = _read_yaml(event_dir / "status.yaml")
        jobid = status_info.get("jobid")
        status = status_info.get("status") or ("submitted" if event in submitted else "pending")
        note = None

        ad = _condor_q_json(jobid)
        if ad is not None:
            status_code = ad.get("JobStatus")
            status = CONDOR_STATUS.get(status_code, status)
            note = ad.get("HoldReason")
        elif jobid:
            final_result = event_dir / "pe" / "final_result"
            if final_result.is_dir() and any(path.suffix == ".hdf5" for path in final_result.iterdir()):
                status = "completed"
            else:
                note = "not found in condor_q"

        rows.append({
            "event": event,
            "status": status,
            "jobid": jobid,
            "request_cpus": None if ad is None else ad.get("RequestCpus"),
            "request_memory_mb": None if ad is None else ad.get("RequestMemory"),
            "remote_host": None if ad is None else ad.get("RemoteHost"),
            "runtime": None if ad is None else _format_runtime(ad.get("RemoteWallClockTime")),
            "note": note,
        })

    return rows


def create_app(project_dir: str | Path, token: str | None = None) -> Flask:
    """Create the Flask application for a project directory."""

    project_path = Path(project_dir).expanduser().resolve()
    app = Flask(__name__)

    @app.before_request
    def _check_token():
        if request.path == "/health":
            return None
        if token is None:
            return None
        supplied = request.headers.get("X-Auth-Token") or request.args.get("token")
        if supplied != token:
            return jsonify({"error": "unauthorized"}), 401
        return None

    @app.get("/")
    def index():
        return HTML_PAGE

    @app.get("/health")
    def health():
        return jsonify({"ok": True})

    @app.get("/api/system")
    def api_system():
        return jsonify(collect_system_info())

    @app.get("/api/jobs")
    def api_jobs():
        return jsonify(collect_jobs(project_path))

    @app.get("/api/summary")
    def api_summary():
        return jsonify({
            "generated_at": time.time(),
            "project_dir": str(project_path),
            "system": collect_system_info(),
            "jobs": collect_jobs(project_path),
        })

    return app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Purohit read-only web monitor.")
    parser.add_argument("--project-dir", required=True, help="Project directory containing submitted_jobs.txt and working/.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address. Use 127.0.0.1 for SSH tunneling or 0.0.0.0 for remote access.")
    parser.add_argument("--port", type=int, default=8765, help="Port to bind.")
    parser.add_argument("--token", default=os.environ.get("PUROHIT_MONITOR_TOKEN"), help="Optional access token. Also read from PUROHIT_MONITOR_TOKEN.")
    parser.add_argument("--debug", action="store_true", help="Enable Flask debug mode.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.host != "127.0.0.1" and not args.token:
        print(
            "WARNING: binding to a non-localhost interface without --token. "
            "Prefer SSH tunneling or set PUROHIT_MONITOR_TOKEN.",
            file=sys.stderr,
        )
    app = create_app(args.project_dir, token=args.token)
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
