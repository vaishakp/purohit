"""Generic run-product publishing for Purohit event pages.

This patch layer keeps the monitor generic.  It discovers publishable files under
both the normal Purohit event directory and any output directory recorded in the
per-event ``status.yaml``.  That covers bilby-style layouts, where plots/logs are
usually under ``project_dir/working/<event>``, and manifest workflows such as
pyRing, where the science output directory may be an absolute path elsewhere.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".svg", ".webp", ".gif"}
FIRST_CLASS_EXTENSIONS = IMAGE_EXTENSIONS | {".html", ".pdf"}
STATUS_OUTPUT_KEYS = ("output", "outdir", "output_dir", "result_dir", "results_dir")


def apply() -> None:
    """Patch static monitor output discovery and event-page rendering."""

    from reanalyze import static_monitor

    if getattr(static_monitor, "_purohit_output_products_patched", False):
        return

    static_monitor.publish_event_outputs = publish_event_outputs
    static_monitor.event_detail_html = event_detail_html
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


def should_publish_product(path: Path, extensions: tuple[str, ...]) -> bool:
    """Return whether a run product should be copied into the webdir.

    We keep checkpoint *state* files out by default because they can be large and
    are not useful in a browser, but checkpoint *plots* are first-class monitor
    products.  Hence a filename containing ``checkpoint`` is allowed when the
    suffix is browser-viewable, such as PNG/PDF/SVG/HTML.
    """

    if not path.is_file():
        return False
    suffix = path.suffix.lower()
    if suffix not in extensions:
        return False
    if "checkpoint" in path.name.lower() and suffix not in FIRST_CLASS_EXTENSIONS:
        return False
    return True


def _event_output_roots(event_dir: Path) -> list[tuple[str, Path]]:
    from reanalyze import static_monitor

    roots: list[tuple[str, Path]] = [("event", event_dir)]
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
        unique.append((label, resolved))
    return unique


def _copy_product(src: Path, dst: Path) -> bool:
    dst.parent.mkdir(parents=True, exist_ok=True)
    from reanalyze import static_monitor

    static_monitor.ensure_web_permissions(dst.parent)
    try:
        shutil.copy2(src, dst)
        static_monitor.ensure_web_permissions(dst)
    except OSError:
        return False
    return True


def _product_priority(item: tuple[str, Path, Path]) -> tuple[int, int, str]:
    _root_label, _root, path = item
    suffix = path.suffix.lower()
    name = path.name.lower()
    first_class = 0 if suffix in FIRST_CLASS_EXTENSIONS else 1
    plot_like = 0 if any(word in name for word in ("plot", "corner", "posterior", "trace", "skymap", "waveform", "diagnostic", "checkpoint")) else 1
    return (first_class, plot_like, str(path))


def publish_event_outputs(event_dir: Path, webdir: Path, event: str, extensions: tuple[str, ...], max_files: int) -> list[dict[str, str]]:
    """Copy event products into the webdir and return browser links.

    The signature matches ``reanalyze.static_monitor.publish_event_outputs`` so
    the existing monitor can call it unchanged.
    """

    from reanalyze import static_monitor

    outputs: list[dict[str, str]] = []
    if max_files <= 0:
        return outputs

    candidates: list[tuple[str, Path, Path]] = []
    seen_paths: set[Path] = set()
    for root_label, root in _event_output_roots(event_dir):
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

    candidates = sorted(candidates, key=_product_priority)[:max_files]
    safe_event = static_monitor.safe_event_dir(event)
    for root_label, root, src in candidates:
        try:
            rel = src.relative_to(root)
        except ValueError:
            continue
        if root == event_dir.resolve():
            artifact_rel = rel
            label = rel.as_posix()
            source = "event"
        else:
            source = _safe_component(root_label)
            artifact_rel = Path(source) / rel
            label = f"{source}/{rel.as_posix()}"
        dst = webdir / "artifacts" / safe_event / artifact_rel
        if not _copy_product(src, dst):
            continue
        suffix = src.suffix.lower()
        outputs.append(
            {
                "label": label,
                "href": f"artifacts/{safe_event}/{artifact_rel.as_posix()}",
                "kind": "image" if suffix in IMAGE_EXTENSIONS else suffix.lstrip(".") or "file",
                "source": source,
            }
        )
    return outputs


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
  <p class="muted">Per-event detail page generated into this event webdir. Image products are previewed inline; every product also has a clickable link.</p>
  <div id="summary" class="card">Loading...</div>
  <h2>Outputs and plots</h2><div id="outputs" class="card"></div>
  <h2>Condor DAG jobs</h2><div id="jobs" class="card"></div>
  <h2>Raw detail JSON</h2><div class="card"><pre id="raw">loading...</pre></div>
<script>
const EVENT = __EVENT_JSON__;
function fmt(x) { return x === null || x === undefined || x === "" ? "—" : x; }
function esc(x) { return String(x ?? "").replace(/[&<>]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;"}[c])); }
function statusClass(status) { return `status-${String(status || "unknown").toLowerCase()}`; }
function row(label, value) { return `<tr><th>${esc(label)}</th><td>${esc(fmt(value))}</td></tr>`; }
function table(rows) { return `<table><tbody>${rows.join("")}</tbody></table>`; }
function isImage(item) { return item.kind === "image" || /\.(png|jpe?g|svg|webp|gif)$/i.test(item.href || ""); }
function outputTable(outputs) {
  if (!outputs || outputs.length === 0) return "No copied output products/logs found yet. The manager only refreshes copied products every plot interval.";
  const previews = outputs.filter(isImage).slice(0, 24);
  const previewHtml = previews.length ? `<div class="preview-grid">${previews.map(item => `<div class="preview-card"><a href="../../${item.href}" target="_blank"><img src="../../${item.href}" alt="${esc(item.label)}"></a><div class="small"><a href="../../${item.href}" target="_blank">${esc(item.label)}</a></div></div>`).join("")}</div>` : "";
  const rows = outputs.map(item => `<tr><td><a href="../../${item.href}" target="_blank">${esc(item.label)}</a></td><td>${esc(fmt(item.kind))}</td><td>${esc(fmt(item.source))}</td></tr>`).join("");
  return `${previewHtml}<table><thead><tr><th>Product / log</th><th>Kind</th><th>Source</th></tr></thead><tbody>${rows}</tbody></table>`;
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
  document.getElementById("summary").innerHTML = table([row("event", EVENT), row("status", job.status), row("DAG cluster id", job.jobid || detail.dag_cluster_id), row("source", job.source), row("runtime", job.runtime), row("remote host", job.remote_host), row("requested CPUs", job.request_cpus), row("requested memory MB", job.request_memory_mb), row("disk / RSS", `${fmt(job.disk_usage)} / ${fmt(job.rss_kb)}`), row("output products", job.output_count), row("detail generated", detail.generated_at ? new Date(detail.generated_at * 1000).toLocaleString() : null), row("live child jobs", detail.live_jobs_count), row("history child jobs", detail.history_jobs_count), row("note", job.note || detail.note)]);
  document.getElementById("outputs").innerHTML = outputTable(job.outputs || []);
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
