"""Event-scoped live product discovery and serving for Purohit.

This module keeps product access generic while avoiding broad filesystem roots.
For each known event, products are discovered only under:

- ``project_dir/working/<event>``;
- output-like directories recorded in ``status.yaml`` via keys such as
  ``output``, ``outdir``, ``output_dir``, ``result_dir``, or ``results_dir``.

The event page stores product metadata and fetches live files through a tokened
API endpoint.  Parent directories and neighboring event directories are never
exposed as browsable roots.
"""

from __future__ import annotations

from http import HTTPStatus
import json
import mimetypes
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".svg", ".webp", ".gif"}
FIRST_CLASS_EXTENSIONS = IMAGE_EXTENSIONS | {".html", ".pdf"}
STATUS_OUTPUT_KEYS = ("output", "outdir", "output_dir", "result_dir", "results_dir")
TEXT_PREVIEW_EXTENSIONS = {".txt", ".log", ".out", ".err", ".json", ".yaml", ".yml", ".ini", ".cfg", ".sub", ".dag", ".sh"}


def apply() -> None:
    """Patch static monitor product discovery and event-page rendering."""

    from reanalyze import static_monitor

    if getattr(static_monitor, "_purohit_output_products_patched", False):
        return

    static_monitor.publish_event_outputs = publish_event_outputs
    static_monitor.event_detail_html = event_detail_html
    static_monitor.collect_jobs = collect_jobs
    static_monitor._purohit_output_products_patched = True


def _safe_component(text: Any) -> str:
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(text or "unknown")).strip("._")
    return name or "unknown"


def _coerce_path(value: Any) -> Path | None:
    if value in (None, ""):
        return None
    try:
        return Path(str(value)).expanduser()
    except (TypeError, ValueError):
        return None


def _event_dir_for(project_dir: Path, event: str) -> Path:
    from reanalyze.tunnel_manager import within_root

    working = project_dir.expanduser().resolve() / "working"
    return within_root(working, Path(event))


def should_publish_product(path: Path, extensions: tuple[str, ...]) -> bool:
    """Return whether a run product is browser-useful.

    Checkpoint plots are allowed, but checkpoint state/restart files stay hidden
    unless a future explicit debugging mode is added.
    """

    if not path.is_file():
        return False
    suffix = path.suffix.lower()
    if suffix not in extensions:
        return False
    if "checkpoint" in path.name.lower() and suffix not in FIRST_CLASS_EXTENSIONS:
        return False
    return True


def event_output_roots(event_dir: Path) -> list[tuple[str, Path]]:
    """Return the narrow, event-scoped read-only roots for one event."""

    from reanalyze import static_monitor

    roots: list[tuple[str, Path]] = [("working", event_dir)]
    status = static_monitor.read_yaml(event_dir / "status.yaml")
    for key in STATUS_OUTPUT_KEYS:
        path = _coerce_path(status.get(key))
        if path is None:
            continue
        if not path.is_absolute():
            path = event_dir / path
        if path.is_file():
            path = path.parent
        if path.is_dir():
            roots.append((key, path))
    seen: set[Path] = set()
    unique: list[tuple[str, Path]] = []
    for label, root in roots:
        try:
            resolved = root.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append((_safe_component(label), resolved))
    return unique


def _product_priority(item: tuple[str, Path, Path]) -> tuple[int, int, float, str]:
    _root_label, _root, path = item
    suffix = path.suffix.lower()
    name = path.name.lower()
    first_class = 0 if suffix in FIRST_CLASS_EXTENSIONS else 1
    plot_like = 0 if any(word in name for word in ("plot", "corner", "posterior", "trace", "skymap", "waveform", "diagnostic", "checkpoint")) else 1
    try:
        mtime = -path.stat().st_mtime
    except OSError:
        mtime = 0.0
    return (first_class, plot_like, mtime, str(path))


def _api_href(event: str, source: str, rel: Path) -> str:
    return "api/event-product?event={event}&source={source}&path={path}".format(
        event=quote(str(event), safe=""),
        source=quote(str(source), safe=""),
        path=quote(rel.as_posix(), safe=""),
    )


def discover_event_products(event_dir: Path, event: str, extensions: tuple[str, ...], max_files: int) -> list[dict[str, Any]]:
    outputs: list[dict[str, Any]] = []
    if max_files <= 0:
        return outputs

    candidates: list[tuple[str, Path, Path]] = []
    seen_paths: set[Path] = set()
    for root_label, root in event_output_roots(event_dir):
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            try:
                resolved = path.resolve()
            except OSError:
                continue
            if resolved in seen_paths:
                continue
            if should_publish_product(path, extensions):
                seen_paths.add(resolved)
                candidates.append((root_label, root, path))

    for root_label, root, src in sorted(candidates, key=_product_priority)[:max_files]:
        try:
            rel = src.relative_to(root)
            stat = src.stat()
        except OSError:
            continue
        suffix = src.suffix.lower()
        outputs.append(
            {
                "label": f"{root_label}/{rel.as_posix()}",
                "source": root_label,
                "path": rel.as_posix(),
                "kind": "image" if suffix in IMAGE_EXTENSIONS else suffix.lstrip(".") or "file",
                "browser_preview": suffix in FIRST_CLASS_EXTENSIONS or suffix in TEXT_PREVIEW_EXTENSIONS,
                "api_href": _api_href(event, root_label, rel),
                "mtime": stat.st_mtime,
                "size": stat.st_size,
            }
        )
    return outputs


def publish_event_outputs(event_dir: Path, webdir: Path, event: str, extensions: tuple[str, ...], max_files: int) -> list[dict[str, Any]]:
    """Compatibility wrapper used by static_monitor.

    In tunnel mode this returns live product metadata and does not copy source
    files.  The ``webdir`` argument is intentionally unused.
    """

    return discover_event_products(event_dir, event, extensions, max_files)


def collect_jobs(project_dir: Path, include_history: bool = True, heartbeat_filename: str = "heartbeat.json", webdir: Path | None = None, copy_outputs: bool = False, output_extensions: tuple[str, ...] = (), max_artifacts_per_event: int = 40) -> list[dict[str, Any]]:
    """Collect jobs with live event-scoped output products every cycle.

    This mirrors ``static_monitor.collect_jobs`` but removes the old coupling
    between output discovery and periodic artifact copying.  Updated plots appear
    on the next monitor cycle because product metadata includes source mtimes.
    """

    from reanalyze import static_monitor

    if not output_extensions:
        output_extensions = static_monitor.DEFAULT_OUTPUT_EXTENSIONS
    submitted = set(static_monitor.read_submitted_jobs(project_dir))
    events = sorted(set(static_monitor.discover_events(project_dir)) | submitted)
    rows: list[dict[str, Any]] = []
    for event in events:
        event_dir = project_dir / "working" / event
        status_info = static_monitor.read_yaml(event_dir / "status.yaml")
        jobid = status_info.get("jobid")
        fallback_status = status_info.get("status") or ("submitted" if event in submitted else "pending")
        ad = static_monitor.condor_q_ad(jobid)
        source = "condor_q" if ad is not None else "local"
        if ad is None and include_history:
            ad = static_monitor.condor_history_ad(jobid)
            if ad is not None:
                source = "condor_history"
        normalized = static_monitor.job_row_from_ad(ad, fallback_status)
        status = normalized["status"]
        note = normalized["note"]
        if ad is None and jobid:
            if static_monitor.final_result_completed(event_dir):
                status = "completed"
                note = "not in condor_q; final result found"
            else:
                note = "not found in condor_q"
        outputs = discover_event_products(event_dir, event, output_extensions, max_artifacts_per_event)
        rows.append({"event": event, "event_page": static_monitor.event_page_href(event), "status": status, "jobid": jobid, "source": source, "request_cpus": normalized["request_cpus"], "request_memory_mb": normalized["request_memory_mb"], "remote_host": normalized["remote_host"], "runtime": normalized["runtime"], "disk_usage": normalized["disk_usage"], "rss_kb": normalized["rss_kb"], "memory_usage_mb": normalized.get("memory_usage_mb"), "rss_mb": normalized.get("rss_mb"), "disk_usage_mb": normalized.get("disk_usage_mb"), "cpu_time": normalized.get("cpu_time"), "cpu_efficiency_percent": normalized.get("cpu_efficiency_percent"), "heartbeat": static_monitor.read_heartbeat(event_dir, heartbeat_filename), "outputs": outputs, "output_count": len(outputs), "note": note})
    return rows


def _resolve_product(handler: Any, event: str, source: str, rel_raw: str) -> Path:
    from reanalyze.tunnel_manager import normalize_rel_path, within_root

    event_dir = _event_dir_for(handler.state.project_dir, event)
    roots = dict(event_output_roots(event_dir))
    if source not in roots:
        raise FileNotFoundError(f"unknown event file source: {source}")
    target = within_root(roots[source], normalize_rel_path(rel_raw))
    if not target.is_file():
        raise FileNotFoundError(rel_raw)
    return target


def serve_event_product(handler: Any, query: dict[str, list[str]]) -> None:
    """Serve one event-scoped file through the tokened tunnel API."""

    if not handler._auth_ok(query):
        handler._send_json({"ok": False, "error": "unauthorized"}, HTTPStatus.UNAUTHORIZED)
        return
    try:
        event = unquote(query.get("event", [""])[-1])
        source = _safe_component(unquote(query.get("source", [""])[-1]))
        rel_raw = unquote(query.get("path", [""])[-1])
        if not event or not source:
            raise FileNotFoundError("event and source are required")
        target = _resolve_product(handler, event, source, rel_raw)
        data = target.read_bytes()
        handler.send_response(HTTPStatus.OK)
        handler._cors()
        handler.send_header("Content-Type", mimetypes.guess_type(str(target))[0] or "application/octet-stream")
        handler.send_header("Content-Length", str(len(data)))
        handler.send_header("Cache-Control", "no-store")
        handler.end_headers()
        handler.wfile.write(data)
    except Exception as exc:  # noqa: BLE001
        handler._send_json({"ok": False, "error": str(exc)}, HTTPStatus.NOT_FOUND)


def event_detail_html(event: str) -> str:
    event_json = json.dumps(event)
    template = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Purohit event detail</title>
  <style>
    :root { color-scheme: light dark; }
    body { font-family: system-ui, -apple-system, BlinkMacSystemFont, sans-serif; margin: 2rem; line-height: 1.35; }
    nav a { margin-right: 1rem; }
    .card { border: 1px solid rgba(128,128,128,0.25); border-radius: 12px; padding: 1rem; margin: 1rem 0; }
    table { width: 100%; border-collapse: collapse; margin-top: 0.5rem; }
    th, td { text-align: left; border-bottom: 1px solid rgba(128,128,128,0.25); padding: 0.45rem; font-size: 0.9rem; vertical-align: top; }
    button { border: 1px solid rgba(128,128,128,0.35); border-radius: 6px; padding: 0.25rem 0.5rem; cursor: pointer; }
    code, pre { padding: 0.1rem 0.25rem; border-radius: 4px; background: rgba(128,128,128,0.15); }
    pre { padding: 0.75rem; white-space: pre-wrap; overflow-x: auto; max-height: 40rem; }
    .muted { opacity: 0.72; }
    .small { font-size: 0.82rem; }
    .preview-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 1rem; margin: 0.5rem 0 1rem 0; }
    .preview-card { border: 1px solid rgba(128,128,128,0.22); border-radius: 10px; padding: 0.6rem; }
    .preview-card img { width: 100%; max-height: 260px; object-fit: contain; display: block; background: rgba(128,128,128,0.08); border-radius: 8px; }
    .status-running { color: #047857; font-weight: 700; }
    .status-completed { color: #2563eb; font-weight: 700; }
    .status-held, .status-removed, .status-failed { color: #b91c1c; font-weight: 700; }
    .status-idle, .status-submitted, .status-pending { color: #92400e; font-weight: 700; }
  </style>
</head>
<body>
  <h1 id="title">Purohit event detail</h1>
  <nav><a href="../../index.html">Monitor</a><a href="../../tunnel.html">Commands</a><a href="../../files.html">Files</a><a href="../../health.html">Health</a></nav>
  <p class="muted">Products are served live from this event's working/output roots only. Parent and sibling directories are not exposed.</p>
  <div id="summary" class="card">Loading...</div>
  <h2>Live outputs and plots</h2><div id="outputs" class="card"></div>
  <h2>Condor DAG jobs</h2><div id="jobs" class="card"></div>
  <h2>Raw detail JSON</h2><div class="card"><pre id="raw">loading...</pre></div>
<script>
const EVENT = __EVENT_JSON__;
const TOKEN_KEY = "purohit_tunnel_token";
const objectUrls = [];
function token() { return localStorage.getItem(TOKEN_KEY) || ""; }
function fmt(x) { return x === null || x === undefined || x === "" ? "—" : x; }
function esc(x) { return String(x ?? "").replace(/[&<>]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;"}[c])); }
function statusClass(status) { return `status-${String(status || "unknown").toLowerCase()}`; }
function row(label, value) { return `<tr><th>${esc(label)}</th><td>${esc(fmt(value))}</td></tr>`; }
function table(rows) { return `<table><tbody>${rows.join("")}</tbody></table>`; }
function isImage(item) { return item.kind === "image"; }
async function fetchBlob(item) {
  const r = await fetch(`../../${item.api_href}&_=${encodeURIComponent(item.mtime || Date.now())}`, {headers: {"X-Purohit-Token": token()}, cache: "no-store"});
  if (r.status === 401) throw new Error("token rejected; open login.html and save the token");
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return await r.blob();
}
async function openProduct(index) {
  const item = currentOutputs[index];
  try {
    const blob = await fetchBlob(item);
    const url = URL.createObjectURL(blob);
    objectUrls.push(url);
    window.open(url, "_blank", "noopener");
  } catch (err) {
    alert(`Could not open product: ${err}`);
  }
}
let currentOutputs = [];
async function loadPreviews(outputs) {
  for (const url of objectUrls.splice(0)) URL.revokeObjectURL(url);
  for (const [index, item] of outputs.entries()) {
    if (!isImage(item)) continue;
    const img = document.getElementById(`preview-${index}`);
    if (!img) continue;
    try {
      const blob = await fetchBlob(item);
      const url = URL.createObjectURL(blob);
      objectUrls.push(url);
      img.src = url;
    } catch (err) {
      img.alt = `preview unavailable: ${err}`;
    }
  }
}
function outputTable(outputs) {
  currentOutputs = outputs || [];
  if (!outputs || outputs.length === 0) return "No live output products/logs found yet.";
  const previews = outputs.filter(isImage).slice(0, 24);
  const previewHtml = previews.length ? `<div class="preview-grid">${previews.map(item => { const index = outputs.indexOf(item); return `<div class="preview-card"><img id="preview-${index}" alt="${esc(item.label)}"><div class="small"><button onclick="openProduct(${index})">Open</button> ${esc(item.label)}</div></div>`; }).join("")}</div>` : "";
  const rows = outputs.map((item, index) => `<tr><td>${esc(item.label)}</td><td>${esc(fmt(item.kind))}</td><td>${esc(fmt(item.source))}</td><td>${item.size || "—"}</td><td>${item.mtime ? new Date(item.mtime * 1000).toLocaleString() : "—"}</td><td><button onclick="openProduct(${index})">Open</button></td></tr>`).join("");
  return `${previewHtml}<table><thead><tr><th>Product / log</th><th>Kind</th><th>Source</th><th>Bytes</th><th>Modified</th><th>Action</th></tr></thead><tbody>${rows}</tbody></table>`;
}
function jobTable(jobs) {
  if (!jobs || jobs.length === 0) return "No DAG subjobs found in condor_q/condor_history.";
  return `<table><thead><tr><th>Job ID</th><th>Status</th><th>Node</th><th>Remote host</th><th>Wall time</th><th>CPU time</th><th>CPU eff.</th><th>Req. CPUs</th><th>Req. mem MB</th><th>Mem MB</th><th>RSS MB</th><th>Disk MB</th><th>Hold reason</th><th>Logs</th></tr></thead><tbody>${jobs.map(j => `<tr><td>${esc(j.job_id)}</td><td class="${statusClass(j.status)}">${esc(fmt(j.status))}</td><td>${esc(fmt(j.node))}</td><td>${esc(fmt(j.remote_host))}</td><td>${esc(fmt(j.runtime))}</td><td>${esc(fmt(j.cpu_time))}</td><td>${esc(fmt(j.cpu_efficiency_percent))}</td><td>${esc(fmt(j.request_cpus))}</td><td>${esc(fmt(j.request_memory_mb))}</td><td>${esc(fmt(j.memory_usage_mb))}</td><td>${esc(fmt(j.rss_mb))}</td><td>${esc(fmt(j.disk_usage_mb))}</td><td>${esc(fmt(j.hold_reason))}</td><td><div>out: <code>${esc(fmt(j.out))}</code></div><div>err: <code>${esc(fmt(j.err))}</code></div><div>log: <code>${esc(fmt(j.log))}</code></div></td></tr>`).join("")}</tbody></table>`;
}
async function refresh() {
  document.getElementById("title").textContent = `Purohit event detail: ${EVENT}`;
  const [statusResp, detailResp] = await Promise.all([fetch(`../../status.json?ts=${Date.now()}`, {cache: "no-store"}), fetch(`../../dag_details.json?ts=${Date.now()}`, {cache: "no-store"})]);
  const status = await statusResp.json();
  const details = await detailResp.json();
  const job = (status.jobs || []).find(j => j.event === EVENT) || {};
  const detail = (details.events || {})[EVENT] || {};
  document.getElementById("summary").innerHTML = table([row("event", EVENT), row("status", job.status), row("DAG cluster id", job.jobid || detail.dag_cluster_id), row("source", job.source), row("runtime", job.runtime), row("remote host", job.remote_host), row("requested CPUs", job.request_cpus), row("requested memory MB", job.request_memory_mb), row("disk / RSS", `${fmt(job.disk_usage)} / ${fmt(job.rss_kb)}`), row("live output products", job.output_count), row("detail generated", detail.generated_at ? new Date(detail.generated_at * 1000).toLocaleString() : null), row("live child jobs", detail.live_jobs_count), row("history child jobs", detail.history_jobs_count), row("note", job.note || detail.note)]);
  document.getElementById("outputs").innerHTML = outputTable(job.outputs || []);
  loadPreviews(job.outputs || []);
  document.getElementById("jobs").innerHTML = jobTable(detail.jobs || []);
  document.getElementById("raw").textContent = JSON.stringify({job, dag: detail}, null, 2);
}
refresh().catch(err => { document.getElementById("summary").textContent = `error: ${err}`; });
setInterval(() => refresh().catch(console.error), 30000);
</script>
</body>
</html>
"""
    return template.replace("__EVENT_JSON__", event_json)
