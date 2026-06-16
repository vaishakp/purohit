"""Event-level static pages and bilby progress extraction for Purohit monitors."""

from __future__ import annotations

import html
import json
from pathlib import Path
import re
import time
from typing import Any

TEXT_EXTENSIONS = {".out", ".err", ".log", ".txt", ".ini", ".yaml", ".yml", ".json", ".dag", ".sub", ".sh"}
LOG_EXTENSIONS = {".out", ".err", ".log"}
PROGRESS_PATTERNS = (
    "dlogz",
    "logz",
    "ncall",
    "nc:",
    "eff",
    "bound:",
    "it:",
    "checkpoint",
    "sampling",
)

EVENT_DETAIL_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Purohit event detail</title>
  <style>
    :root { color-scheme: light dark; }
    body { font-family: system-ui, -apple-system, BlinkMacSystemFont, sans-serif; margin: 2rem; line-height: 1.35; }
    h1, h2 { margin-bottom: 0.4rem; }
    .muted { opacity: 0.72; }
    .card { border: 1px solid rgba(128,128,128,0.25); border-radius: 12px; padding: 1rem; margin: 1rem 0; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 1rem; }
    table { width: 100%; border-collapse: collapse; margin-top: 0.5rem; }
    th, td { text-align: left; border-bottom: 1px solid rgba(128,128,128,0.25); padding: 0.5rem; font-size: 0.9rem; vertical-align: top; }
    code, pre { padding: 0.1rem 0.25rem; border-radius: 4px; background: rgba(128,128,128,0.15); }
    pre { white-space: pre-wrap; padding: 0.75rem; overflow-x: auto; max-height: 32rem; }
    a { text-decoration: none; }
    button { margin: 0.08rem; border: 1px solid rgba(128,128,128,0.35); border-radius: 6px; padding: 0.25rem 0.45rem; cursor: pointer; }
    .small { font-size: 0.82rem; }
    .warn { border-color: #92400e; background: rgba(146,64,14,0.08); }
  </style>
</head>
<body>
  <p><a href="../../index.html">← Back to monitor</a></p>
  <h1 id="event-title">Event detail</h1>
  <div id="summary" class="card">Loading...</div>
  <h2>Latest bilby / sampler progress</h2>
  <div id="progress" class="card"></div>
  <h2>Files</h2>
  <div id="files" class="card"></div>
  <h2>Preview</h2>
  <div id="preview" class="card"><p class="muted">Click “Preview” next to a text-like file.</p></div>
<script>
function fmt(x) { return x === null || x === undefined || x === "" ? "—" : x; }
function dt(ts) { return ts ? new Date(ts * 1000).toLocaleString() : "—"; }
function esc(s) { return String(s ?? "").replace(/[&<>]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;"}[c])); }
function categoryTitle(c) {
  const map = {"analysis":"Bilby analysis / sampler", "data_generation":"Data generation", "dag_submit":"DAG / submit files", "results":"Results / plots", "logs":"Other logs", "other":"Other files"};
  return map[c] || c;
}
async function previewFile(href, label) {
  const box = document.getElementById("preview");
  box.innerHTML = `<p class="muted">Loading ${esc(label)}...</p>`;
  try {
    const response = await fetch(href + `?ts=${Date.now()}`, {cache: "no-store"});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const text = await response.text();
    box.innerHTML = `<h3>${esc(label)}</h3><p><a href="${href}">Open raw file</a></p><pre>${esc(text.slice(-200000))}</pre>`;
  } catch (err) {
    box.innerHTML = `<p>Preview failed: ${esc(err)}</p><p><a href="${href}">Open raw file</a></p>`;
  }
}
async function refresh() {
  const response = await fetch(`files.json?ts=${Date.now()}`, {cache: "no-store"});
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const data = await response.json();
  document.title = `Purohit event ${data.event}`;
  document.getElementById("event-title").textContent = data.event;
  document.getElementById("summary").innerHTML = `<div><strong>Status:</strong> ${fmt(data.status)}</div><div><strong>Cluster ID:</strong> ${fmt(data.jobid)}</div><div><strong>Generated:</strong> ${dt(data.generated_at)}</div><div><strong>Event directory:</strong> <code>${esc(data.event_dir)}</code></div>`;
  const p = data.latest_progress || {};
  const age = p.age_seconds == null ? "—" : `${Math.round(p.age_seconds)} s`;
  const parsed = p.parsed || {};
  document.getElementById("progress").className = `card ${p.stale ? "warn" : ""}`;
  document.getElementById("progress").innerHTML = p.line ? `<div><strong>File:</strong> <code>${esc(p.file || "")}</code></div><div><strong>Modified:</strong> ${dt(p.mtime)}; age ${age}</div><div class="grid"><div><strong>dlogz:</strong> ${fmt(parsed.dlogz)}</div><div><strong>logz:</strong> ${fmt(parsed.logz)}</div><div><strong>iteration:</strong> ${fmt(parsed.iteration)}</div><div><strong>ncall:</strong> ${fmt(parsed.ncall)}</div><div><strong>efficiency:</strong> ${fmt(parsed.efficiency)}</div></div><pre>${esc(p.line)}</pre>` : `<p class="muted">No progress line found yet.</p>`;
  const groups = {};
  for (const f of data.files || []) { (groups[f.category] ||= []).push(f); }
  document.getElementById("files").innerHTML = Object.entries(groups).map(([category, files]) => `<h3>${categoryTitle(category)}</h3><table><thead><tr><th>File</th><th>Size</th><th>Modified</th><th>Actions</th></tr></thead><tbody>${files.map(f => `<tr><td><a href="${f.href}">${esc(f.label)}</a></td><td>${fmt(f.size_bytes)}</td><td>${dt(f.mtime)}</td><td>${f.text_preview ? `<button onclick="previewFile('${f.href}', '${esc(f.label).replace(/'/g, "&#39;")}')">Preview</button>` : "—"}</td></tr>`).join("")}</tbody></table>`).join("") || `<p>No copied files found.</p>`;
}
refresh().catch(err => { document.getElementById("summary").textContent = `error: ${err}`; });
setInterval(() => refresh().catch(console.error), 30000);
</script>
</body>
</html>
"""


def tail_text(path: Path, max_bytes: int = 262_144) -> str:
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > max_bytes:
                handle.seek(-max_bytes, 2)
            raw = handle.read()
    except OSError:
        return ""
    return raw.decode("utf-8", errors="replace")


def split_log_lines(text: str) -> list[str]:
    return [line.strip() for line in re.split(r"[\r\n]+", text) if line.strip()]


def parse_progress_values(line: str) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    patterns = {
        "dlogz": r"dlogz[:=]\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)",
        "logz": r"(?<!d)logz[:=]\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)",
        "iteration": r"(?:it|iter|iteration)[:=]\s*(\d+)",
        "ncall": r"(?:ncall|nc)[:=]\s*([0-9.eE+-]+)",
        "efficiency": r"(?:eff|efficiency)[:=]\s*([0-9.eE+-]+%?)",
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, line, flags=re.IGNORECASE)
        if match:
            parsed[key] = match.group(1)
    return parsed


def latest_progress_line(event_dir: Path, stale_after_seconds: int = 1800) -> dict[str, Any] | None:
    if not event_dir.is_dir():
        return None
    candidates = [p for p in event_dir.rglob("*") if p.is_file() and p.suffix.lower() in LOG_EXTENSIONS and "checkpoint" not in p.name.lower()]
    if not candidates:
        return None

    def priority(path: Path) -> tuple[int, float]:
        lower = str(path).lower()
        score = 0
        if "analysis" in lower or "bilby" in lower or "dynesty" in lower or "sampler" in lower:
            score += 4
        if path.suffix.lower() == ".out":
            score += 2
        if "data" in lower and "analysis" not in lower:
            score -= 1
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0.0
        return score, mtime

    for path in sorted(candidates, key=priority, reverse=True):
        lines = split_log_lines(tail_text(path))
        if not lines:
            continue
        chosen = None
        for line in reversed(lines):
            lower = line.lower()
            if any(pattern in lower for pattern in PROGRESS_PATTERNS):
                chosen = line
                break
        if chosen is None:
            chosen = lines[-1]
        try:
            mtime = path.stat().st_mtime
            age = time.time() - mtime
            rel = str(path.relative_to(event_dir))
        except OSError:
            mtime = None
            age = None
            rel = str(path)
        return {
            "file": rel,
            "line": chosen,
            "mtime": mtime,
            "age_seconds": age,
            "stale": bool(age is not None and age > stale_after_seconds),
            "parsed": parse_progress_values(chosen),
        }
    return None


def categorize_file(label: str) -> str:
    lower = label.lower()
    if any(token in lower for token in ("data_generation", "generation", "data_dump", "datadump")):
        return "data_generation"
    if lower.endswith((".dag", ".sub", ".submit", ".sh")) or "dag" in lower:
        return "dag_submit"
    if any(token in lower for token in ("analysis", "bilby", "dynesty", "sampler")) and lower.endswith((".out", ".err", ".log")):
        return "analysis"
    if lower.endswith((".png", ".jpg", ".jpeg", ".svg", ".pdf", ".html", ".hdf5", ".pkl", ".pickle")) or "final_result" in lower or "result" in lower:
        return "results"
    if lower.endswith((".out", ".err", ".log")):
        return "logs"
    return "other"


def write_event_detail_page(webdir: Path, event: str, payload: dict[str, Any]) -> None:
    event_webdir = webdir / "events" / event
    event_webdir.mkdir(parents=True, exist_ok=True)
    atomic_write_text = payload.pop("_atomic_write_text")
    atomic_write_json = payload.pop("_atomic_write_json")
    atomic_write_text(event_webdir / "index.html", EVENT_DETAIL_HTML)
    atomic_write_json(event_webdir / "files.json", payload)


def build_event_files_payload(event_dir: Path, webdir: Path, event: str, outputs: list[dict[str, str]], status_row: dict[str, Any]) -> dict[str, Any]:
    files = []
    for item in outputs:
        label = item.get("label", "")
        href = "../../" + item.get("href", "")
        src = event_dir / label
        try:
            stat = src.stat()
            size = stat.st_size
            mtime = stat.st_mtime
        except OSError:
            size = None
            mtime = None
        suffix = Path(label).suffix.lower()
        files.append({
            "label": label,
            "href": href,
            "size_bytes": size,
            "mtime": mtime,
            "category": categorize_file(label),
            "text_preview": suffix in TEXT_EXTENSIONS,
        })
    files.sort(key=lambda f: (f["category"], f["label"]))
    return {
        "generated_at": time.time(),
        "event": event,
        "event_dir": str(event_dir),
        "status": status_row.get("status"),
        "jobid": status_row.get("jobid"),
        "latest_progress": latest_progress_line(event_dir) or {},
        "files": files,
    }
