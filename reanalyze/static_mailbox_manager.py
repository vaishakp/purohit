"""Static manager variant that drains a CGI-host-local mailbox.

This supports deployments where the CGI host can write to its local /tmp or
/var/tmp, but that directory is not mounted on the submit/login host. The
manager pulls commands by HTTP(S) from the CGI endpoint and executes them on the
submit/login host.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile
import time
from typing import Any
from urllib import request
from urllib.error import HTTPError, URLError

from reanalyze.manager_health import build_health_payload, publish_health_files, sanitize_command_result
from reanalyze.static_manager import append_audit, process_command
from reanalyze.static_monitor import publish_once

CONTROL_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Purohit command controls</title>
  <style>
    :root { color-scheme: light dark; }
    body { font-family: system-ui, -apple-system, BlinkMacSystemFont, sans-serif; margin: 2rem; line-height: 1.35; }
    .card { border: 1px solid rgba(128,128,128,0.25); border-radius: 12px; padding: 1rem; margin: 1rem 0; }
    .warn { border-color: #b91c1c; background: rgba(185,28,28,0.08); }
    table { width: 100%; border-collapse: collapse; margin-top: 0.5rem; }
    th, td { text-align: left; border-bottom: 1px solid rgba(128,128,128,0.25); padding: 0.55rem; font-size: 0.9rem; vertical-align: top; }
    code, input { padding: 0.15rem 0.35rem; border-radius: 4px; background: rgba(128,128,128,0.15); }
    input { min-width: 24rem; border: 1px solid rgba(128,128,128,0.35); }
    .btn { margin: 0.08rem; border: 1px solid rgba(128,128,128,0.35); border-radius: 6px; padding: 0.32rem 0.55rem; cursor: pointer; transition: background 120ms ease, transform 80ms ease, opacity 120ms ease; }
    .btn:hover:not(:disabled) { transform: translateY(-1px); }
    .btn:disabled { cursor: wait; opacity: 0.75; }
    .btn-primary { background: rgba(37,99,235,0.14); border-color: rgba(37,99,235,0.35); }
    .btn-danger { background: rgba(185,28,28,0.12); border-color: rgba(185,28,28,0.35); }
    .btn-reset { background: rgba(146,64,14,0.12); border-color: rgba(146,64,14,0.35); }
    .btn-pending { background: rgba(146,64,14,0.18); border-color: rgba(146,64,14,0.55); }
    .btn-success { background: rgba(4,120,87,0.20); border-color: rgba(4,120,87,0.70); color: #047857; font-weight: 700; }
    .btn-error { background: rgba(185,28,28,0.18); border-color: rgba(185,28,28,0.70); color: #b91c1c; font-weight: 700; }
    .status { margin-top: 0.75rem; padding: 0.55rem 0.7rem; border-radius: 8px; border: 1px solid rgba(128,128,128,0.25); }
    .status-muted { opacity: 0.78; }
    .status-pending { border-color: rgba(146,64,14,0.45); background: rgba(146,64,14,0.10); }
    .status-ok { border-color: rgba(4,120,87,0.55); background: rgba(4,120,87,0.10); }
    .status-error { border-color: rgba(185,28,28,0.55); background: rgba(185,28,28,0.10); }
    .token-row { display: flex; flex-wrap: wrap; gap: 0.5rem; align-items: center; }
    .muted { opacity: 0.72; }
    .small { font-size: 0.82rem; }
    #lb-warning { display: none; }
  </style>
</head>
<body>
  <h1>Purohit command controls</h1>
  <p class="muted">This page POSTs commands to the CGI mailbox endpoint. The static manager on the submit host drains the mailbox and executes commands on its next polling pass.</p>
  <p><a href="health.html">Manager health diagnostics</a></p>
  <div id="lb-warning" class="card warn"></div>
  <div class="card">
    <div><strong>Mailbox URL:</strong> <code id="mailbox-url">loading...</code></div>
    <div><strong>Last manager drain host:</strong> <code id="last-drain-host">unknown</code></div>
    <div><strong>Observed CGI hosts:</strong> <code id="observed-hosts">unknown</code></div>
    <div><strong>Last browser enqueue host:</strong> <code id="last-enqueue-host">unknown</code></div>
    <div class="token-row">
      <strong>Command token:</strong>
      <input id="token" type="password" placeholder="optional token" oninput="markTokenUnsaved()">
      <button id="save-token-button" class="btn btn-primary" onclick="saveToken(this)">Save in this browser</button>
      <span id="token-status" class="small muted">Not saved in this browser.</span>
    </div>
    <div id="result" class="status status-muted small">No command queued in this browser session yet.</div>
  </div>
  <div class="card">
    <table><thead><tr><th>Event</th><th>Status</th><th>Cluster ID</th><th>Controls</th></tr></thead><tbody id="jobs"></tbody></table>
  </div>
<script>
let mailboxUrl = null;
let mailboxConfig = {};
const ORIGINAL_LABEL = "data-original-label";
function fmt(x) { return x === null || x === undefined || x === "" ? "—" : x; }
function setStatus(message, kind="muted") {
  const result = document.getElementById("result");
  result.className = `status status-${kind} small`;
  result.textContent = message;
}
function setButtonState(button, state, label) {
  if (!button) return;
  if (!button.hasAttribute(ORIGINAL_LABEL)) button.setAttribute(ORIGINAL_LABEL, button.textContent);
  button.classList.remove("btn-pending", "btn-success", "btn-error");
  if (state) button.classList.add(`btn-${state}`);
  if (label) button.textContent = label;
  button.disabled = state === "pending";
}
function restoreButton(button, delay=1800) {
  if (!button) return;
  const original = button.getAttribute(ORIGINAL_LABEL) || button.textContent;
  setTimeout(() => {
    button.classList.remove("btn-pending", "btn-success", "btn-error");
    button.textContent = original;
    button.disabled = false;
  }, delay);
}
function markTokenUnsaved() {
  const status = document.getElementById("token-status");
  const saved = localStorage.getItem("purohit_mailbox_token") || "";
  const current = document.getElementById("token").value || "";
  status.textContent = current && current === saved ? "Token is saved in this browser." : "Token edited; click Save to persist it in this browser.";
}
function saveToken(button) {
  const token = document.getElementById("token").value || "";
  localStorage.setItem("purohit_mailbox_token", token);
  document.getElementById("token-status").textContent = token ? "Token saved in this browser." : "Empty token saved; token auth may fail.";
  setButtonState(button, "success", "Saved ✓");
  setStatus(token ? "Token saved in this browser. You can now use the command buttons." : "Empty token saved. If the CGI requires a token, commands will still fail as unauthorized.", token ? "ok" : "pending");
  restoreButton(button);
}
function controls(event) {
  return `<button class="btn btn-primary" onclick="sendCommand(this,'submit_event','${event}')">Submit</button><button class="btn" onclick="sendCommand(this,'hold_event','${event}')">Hold</button><button class="btn" onclick="sendCommand(this,'release_event','${event}')">Release</button><button class="btn btn-danger" onclick="sendCommand(this,'remove_event','${event}')">Remove</button><button class="btn btn-reset" onclick="sendCommand(this,'reset_event','${event}')">Reset</button>`;
}
function updateLoadBalanceWarning() {
  const warning = document.getElementById("lb-warning");
  const lastEnqueueHost = localStorage.getItem("purohit_last_enqueue_host") || "";
  const lastDrainHost = mailboxConfig.last_drain_host || "";
  const observedHosts = mailboxConfig.observed_hosts || [];
  const detected = mailboxConfig.load_balancing_detected || (lastEnqueueHost && lastDrainHost && lastEnqueueHost !== lastDrainHost);
  document.getElementById("last-drain-host").textContent = lastDrainHost || "unknown";
  document.getElementById("observed-hosts").textContent = observedHosts.length ? observedHosts.join(", ") : "unknown";
  document.getElementById("last-enqueue-host").textContent = lastEnqueueHost || "unknown";
  if (detected) {
    warning.style.display = "block";
    warning.innerHTML = `<strong>Load-balancing warning:</strong> CGI requests are reaching multiple backend hosts, or your last command was queued on a different host from the manager's last drain. Host-local /tmp or /var/tmp mailboxes may strand commands until the manager reaches the same backend. Observed hosts: <code>${observedHosts.join(", ") || "unknown"}</code>; last enqueue: <code>${lastEnqueueHost || "unknown"}</code>; last drain: <code>${lastDrainHost || "unknown"}</code>.`;
  } else {
    warning.style.display = "none";
    warning.textContent = "";
  }
}
async function sendCommand(button, action, event) {
  if (!mailboxUrl) {
    setStatus("Cannot queue command: mailbox URL is not configured yet.", "error");
    setButtonState(button, "error", "No URL ✗");
    restoreButton(button);
    return;
  }
  if (action === "remove_event" && !confirm(`Remove ${event}?`)) return;
  if (action === "reset_event" && !confirm(`Reset ${event}? This clears the submitted ledger entry, resets status.yaml, and removes the event pe directory if present. It does not automatically submit a new job.`)) return;
  const token = localStorage.getItem("purohit_mailbox_token") || document.getElementById("token").value || "";
  setButtonState(button, "pending", "Queueing…");
  setStatus(`Queueing ${action} for ${event}...`, "pending");
  try {
    const response = await fetch(mailboxUrl, {method: "POST", headers: {"Content-Type": "application/json", "X-Purohit-Token": token}, body: JSON.stringify({action, event, token})});
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.error || `HTTP ${response.status}`);
    if (data.cgi_host) localStorage.setItem("purohit_last_enqueue_host", data.cgi_host);
    if (data.queued && data.queued.id) localStorage.setItem("purohit_last_command_id", data.queued.id);
    setButtonState(button, "success", "Queued ✓");
    setStatus(`Queued ${action} for ${event} on ${data.cgi_host || "unknown CGI host"}. Command ID: ${(data.queued && data.queued.id) || "unknown"}. The manager will execute it on the next polling pass.`, "ok");
    updateLoadBalanceWarning();
    restoreButton(button, 2200);
  } catch (err) {
    setButtonState(button, "error", "Failed ✗");
    setStatus(`Command failed for ${event}: ${err}`, "error");
    restoreButton(button, 3500);
  }
}
async function refresh() {
  const response = await fetch(`mailbox_status.json?ts=${Date.now()}`, {cache: "no-store"});
  mailboxConfig = await response.json();
  mailboxUrl = mailboxConfig.mailbox_url;
  document.getElementById("mailbox-url").textContent = mailboxUrl || "not configured";
  if (!document.getElementById("token").value) document.getElementById("token").value = localStorage.getItem("purohit_mailbox_token") || "";
  markTokenUnsaved();
  updateLoadBalanceWarning();
  const statusResponse = await fetch(`status.json?ts=${Date.now()}`, {cache: "no-store"});
  const status = await statusResponse.json();
  const rows = (status.jobs || []).map(job => `<tr><td>${fmt(job.event)}</td><td>${fmt(job.status)}</td><td>${fmt(job.jobid)}</td><td>${controls(job.event)}</td></tr>`).join("");
  document.getElementById("jobs").innerHTML = rows || `<tr><td colspan="4">No jobs found.</td></tr>`;
}
refresh().catch(err => { setStatus(`error: ${err}`, "error"); });
setInterval(() => refresh().catch(err => setStatus(`refresh error: ${err}`, "error")), 30000);
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
    atomic_write_text(path, json.dumps(data, indent=2, sort_keys=True) + "\n", mode=mode)


def call_mailbox(mailbox_url: str, payload: dict[str, Any], token: str | None = None, timeout: int = 30) -> dict[str, Any]:
    if token:
        payload = {**payload, "token": token}
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(mailbox_url, data=body, method="POST", headers={"Content-Type": "application/json"})
    if token:
        req.add_header("X-Purohit-Token", token)
    with request.urlopen(req, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
    return data if isinstance(data, dict) else {"ok": False, "error": "mailbox response was not a JSON object"}


def drain_mailbox(mailbox_url: str, token: str | None = None, timeout: int = 30) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        data = call_mailbox(mailbox_url, {"mode": "drain"}, token=token, timeout=timeout)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        return ([{"action": "invalid", "error": f"mailbox drain failed: {exc}"}], {"error": str(exc)})
    if not data.get("ok"):
        return ([{"action": "invalid", "error": f"mailbox drain failed: {data}"}], data)
    commands = data.get("commands", [])
    return ([item for item in commands if isinstance(item, dict)] if isinstance(commands, list) else [], data)


def probe_mailbox_hosts(mailbox_url: str, token: str | None = None, probes: int = 3, timeout: int = 30) -> dict[str, Any]:
    responses: list[dict[str, Any]] = []
    hosts: list[str] = []
    errors: list[str] = []
    for _ in range(max(0, probes)):
        try:
            data = call_mailbox(mailbox_url, {"mode": "status"}, token=token, timeout=timeout)
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            errors.append(str(exc))
            continue
        responses.append(data)
        host = data.get("cgi_host")
        if host and host not in hosts:
            hosts.append(host)
    return {
        "observed_hosts": hosts,
        "status_probe_responses": responses,
        "status_probe_errors": errors,
        "load_balancing_detected": len(hosts) > 1,
    }


def process_remote_commands(project_dir: Path, mailbox_url: str, token: str | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    commands, drain_metadata = drain_mailbox(mailbox_url, token=token)
    results: list[dict[str, Any]] = []
    for command in commands:
        result = process_command(project_dir, command) if command.get("action") != "invalid" else {"ok": False, "command": command, "message": command.get("error")}
        append_audit(project_dir, result)
        results.append(result)
    return results, drain_metadata


def publish_control_page(webdir: Path, mailbox_url: str, mailbox_metadata: dict[str, Any] | None = None) -> None:
    webdir = webdir.expanduser().resolve()
    metadata = mailbox_metadata or {}
    atomic_write_text(webdir / "commands.html", CONTROL_HTML)
    atomic_write_json(
        webdir / "mailbox_status.json",
        {
            "mailbox_url": mailbox_url,
            "generated_at": time.time(),
            **metadata,
        },
    )


def build_mailbox_metadata(drain_metadata: dict[str, Any], probe_metadata: dict[str, Any]) -> dict[str, Any]:
    observed_hosts = list(probe_metadata.get("observed_hosts", []))
    last_drain_host = drain_metadata.get("cgi_host")
    if last_drain_host and last_drain_host not in observed_hosts:
        observed_hosts.append(last_drain_host)
    return {
        "last_drain_host": last_drain_host,
        "last_drain_spool_dir": drain_metadata.get("spool_dir"),
        "last_drain_command_file": drain_metadata.get("command_file"),
        "observed_hosts": observed_hosts,
        "load_balancing_detected": bool(probe_metadata.get("load_balancing_detected") or len(observed_hosts) > 1),
        "status_probe_responses": probe_metadata.get("status_probe_responses", []),
        "status_probe_errors": probe_metadata.get("status_probe_errors", []),
        "last_probe_at": time.time(),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run static Purohit manager with CGI mailbox draining.")
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument("--webdir", required=True, type=Path)
    parser.add_argument("--mailbox-url", required=True, help="CGI mailbox endpoint URL, e.g. https://.../purohit_mailbox.cgi")
    parser.add_argument("--token-file", type=Path, default=None, help="Optional local token file also accepted by CGI.")
    parser.add_argument("--env-mode", choices=["names", "redacted", "full"], default="redacted", help="Environment variables shown on health.html. Use full only for private/non-public webdirs.")
    parser.add_argument("--command-result-tail", type=int, default=100, help="Number of recent command results to publish.")
    parser.add_argument("--mailbox-status-probes", type=int, default=3, help="Number of CGI status probes per manager cycle for load-balancing detection.")
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument("--plot-interval", type=int, default=300)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--no-history", action="store_true")
    parser.add_argument("--heartbeat-filename", default="heartbeat.json")
    parser.add_argument("--max-artifacts-per-event", type=int, default=40)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    project_dir = args.project_dir.expanduser().resolve()
    webdir = args.webdir.expanduser().resolve()
    token = args.token_file.expanduser().read_text().strip() if args.token_file and args.token_file.is_file() else None
    manager_started_at = time.time()
    last_cycle_at: float | None = None
    last_cycle_duration_s: float | None = None
    last_plot_publish = 0.0
    recent_command_results: list[dict[str, Any]] = []
    last_error: str | None = None
    while True:
        cycle_start = time.time()
        mailbox_metadata: dict[str, Any] = {}
        try:
            probe_metadata = probe_mailbox_hosts(args.mailbox_url, token=token, probes=args.mailbox_status_probes)
            results, drain_metadata = process_remote_commands(project_dir, args.mailbox_url, token=token)
            mailbox_metadata = build_mailbox_metadata(drain_metadata, probe_metadata)
            recent_command_results.extend(sanitize_command_result(result) for result in results)
            if args.command_result_tail > 0:
                recent_command_results = recent_command_results[-args.command_result_tail :]
            now = time.time()
            copy_outputs = now - last_plot_publish >= args.plot_interval
            payload = publish_once(
                project_dir,
                webdir,
                include_history=not args.no_history,
                heartbeat_filename=args.heartbeat_filename,
                copy_outputs=copy_outputs,
                command_file=None,
                max_artifacts_per_event=args.max_artifacts_per_event,
            )
            publish_control_page(webdir, args.mailbox_url, mailbox_metadata=mailbox_metadata)
            if copy_outputs:
                last_plot_publish = now
            last_error = None
            warning = " LOAD-BALANCING-DETECTED" if mailbox_metadata["load_balancing_detected"] else ""
            print(f"Drained {len(results)} command(s); published {len(payload['jobs'])} jobs to {webdir} at {time.strftime('%Y-%m-%d %H:%M:%S')}{warning}")
        except Exception as exc:  # noqa: BLE001 - long-running monitor should publish health even on failures
            last_error = str(exc)
            print(f"Manager cycle failed: {last_error}")
        finally:
            last_cycle_at = time.time()
            last_cycle_duration_s = last_cycle_at - cycle_start
            health_payload = build_health_payload(
                project_dir=project_dir,
                webdir=webdir,
                manager_started_at=manager_started_at,
                last_cycle_at=last_cycle_at,
                last_cycle_duration_s=last_cycle_duration_s,
                interval_s=args.interval,
                plot_interval_s=args.plot_interval,
                mailbox_metadata=mailbox_metadata,
                last_artifact_publish_at=last_plot_publish if last_plot_publish else None,
                command_results_count=len(recent_command_results),
                env_mode=args.env_mode,
                last_error=last_error,
            )
            publish_health_files(
                webdir,
                health_payload,
                recent_command_results,
                atomic_write_text=atomic_write_text,
                atomic_write_json=atomic_write_json,
            )
        if args.once:
            return
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
