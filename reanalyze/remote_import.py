"""Explicit source-host to target-cluster event materialization.

This module keeps remote import separate from job submission. It is intended for
workflows such as:

1. run expensive config discovery on a source host where the large tree lives;
2. copy only selected INIs and referenced input files to a target project;
3. write a target-cluster config and manifest;
4. submit locally from the target cluster using the existing manager.
"""

from __future__ import annotations

from dataclasses import dataclass
import ast
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import shlex
import shutil
import subprocess
import tempfile
import time
from typing import Any

import yaml

from reanalyze.host_profiles import HostProfile, HostProfiles
from reanalyze.input_staging import PATH_KEY_HINTS, DEFAULT_PRESERVE_ROOTS, is_probable_path, split_pathlike_value


@dataclass(frozen=True)
class Dependency:
    key: str
    source_path: str
    kind: str


@dataclass(frozen=True)
class EventImportResult:
    event: str
    source_ini: str
    original_ini: Path
    submit_ini: Path
    manifest_path: Path
    dependencies: tuple[dict[str, Any], ...]


def run_checked(command: list[str], *, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, input=input_text, check=True, capture_output=True, text=True)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def quote_remote(path: str) -> str:
    return shlex.quote(path)


def rsync_pull(source_host: HostProfile, source_path: str, target_path: Path, rsync_args: list[str] | None = None) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    args = rsync_args or ["-a", "--partial", "--protect-args"]
    run_checked(["rsync", *args, f"{source_host.require_ssh()}:{source_path}", str(target_path)])


def run_remote_python(source_host: HostProfile, code: str, args: list[str]) -> str:
    command = ["ssh", source_host.require_ssh(), "python3", "-", *args]
    out = run_checked(command, input_text=code)
    return out.stdout


REMOTE_DISCOVERY_CODE = r'''
from __future__ import annotations
import json, os, re, sys
from pathlib import Path
source_dir = Path(sys.argv[1]).expanduser()
apx = sys.argv[2].lower()
events = set(item for item in sys.argv[3].split(',') if item) if len(sys.argv) > 3 else set()
files = []
for path in source_dir.rglob('*.ini'):
    if apx and apx not in path.name.lower():
        continue
    try:
        rel = path.relative_to(source_dir)
    except ValueError:
        continue
    event = rel.parts[0] if rel.parts else path.parent.name
    if events and event not in events:
        continue
    files.append((event, str(path)))
selected = {}
for event, path in sorted(files):
    selected.setdefault(event, path)
print(json.dumps({'source_dir': str(source_dir), 'events': {event: {'source_ini': path} for event, path in selected.items()}}, sort_keys=True))
'''


def discover_remote_configs(source_host: HostProfile, source_dir: str, apx: str, events: list[str] | None = None) -> dict[str, Any]:
    stdout = run_remote_python(source_host, REMOTE_DISCOVERY_CODE, [source_dir, apx, ",".join(events or [])])
    return json.loads(stdout)


def parse_ini_dependencies_text(text: str, *, preserve_roots: list[str] | None = None, path_key_hints: tuple[str, ...] = PATH_KEY_HINTS) -> list[Dependency]:
    preserve = tuple(preserve_roots or DEFAULT_PRESERVE_ROOTS)
    deps: list[Dependency] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", ";", "[")) or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if not any(hint in key.lower() for hint in path_key_hints):
            continue
        for candidate in split_pathlike_value(value.strip()):
            expanded = os.path.expandvars(os.path.expanduser(candidate.strip()))
            if not is_probable_path(expanded) or not expanded.startswith("/"):
                continue
            if any(expanded == root or expanded.startswith(root.rstrip("/") + "/") for root in preserve):
                continue
            kind = classify_dependency_key(key)
            deps.append(Dependency(key=key, source_path=expanded, kind=kind))
    return deps


def classify_dependency_key(key: str) -> str:
    lower = key.lower()
    for kind in ("psd", "calibration", "prior", "injection", "roq", "basis", "weights", "lookup", "data"):
        if kind in lower:
            return kind
    return "input"


def home_relative_path(path: str, source_home: Path) -> PurePosixPath:
    raw = PurePosixPath(path)
    home = PurePosixPath(str(source_home))
    try:
        rel = raw.relative_to(home)
    except ValueError:
        rel = PurePosixPath(str(raw).lstrip("/"))
    return rel


def event_data_path(target_project_dir: Path, event: str, source_path: str, source_home: Path, data_subdir: str = "data") -> Path:
    rel = home_relative_path(source_path, source_home)
    return target_project_dir / "working" / event / data_subdir / "home-relative" / rel


def replace_paths(text: str, replacements: dict[str, str]) -> str:
    out = text
    for old in sorted(replacements, key=len, reverse=True):
        out = out.replace(old, replacements[old])
    return out


def materialize_event(
    *,
    event: str,
    source_ini: str,
    source_host: HostProfile,
    target_host: HostProfile,
    target_project_dir: Path,
    preserve_roots: list[str] | None = None,
    data_subdir: str = "data",
    submit_suffix: str = ".target.ini",
    rsync_args: list[str] | None = None,
) -> EventImportResult:
    event_dir = target_project_dir / "working" / event
    original_dir = event_dir / "original"
    original_dir.mkdir(parents=True, exist_ok=True)
    original_ini = original_dir / (Path(source_ini).name.replace(".ini", ".source.ini"))
    rsync_pull(source_host, source_ini, original_ini, rsync_args=rsync_args)

    ini_text = original_ini.read_text()
    dependencies = parse_ini_dependencies_text(ini_text, preserve_roots=preserve_roots)
    replacements: dict[str, str] = {}
    copied: list[dict[str, Any]] = []
    for dep in dependencies:
        target_path = event_data_path(target_project_dir, event, dep.source_path, source_host.require_home(), data_subdir=data_subdir)
        rsync_pull(source_host, dep.source_path, target_path, rsync_args=rsync_args)
        replacements[dep.source_path] = str(target_path)
        copied.append({
            "key": dep.key,
            "kind": dep.kind,
            "source_path": dep.source_path,
            "target_path": str(target_path),
            "size_bytes": target_path.stat().st_size if target_path.exists() else None,
            "sha256": sha256_file(target_path) if target_path.is_file() else None,
        })

    # Home-preserving rewrite for remaining project/output paths. Dependency paths
    # copied into event data win because replacements are applied first.
    source_home = str(source_host.require_home()).rstrip("/")
    target_home = str(target_host.require_home()).rstrip("/")
    replacements[source_home + "/"] = target_home + "/"
    rewritten = replace_paths(ini_text, replacements)

    submit_ini = event_dir / (Path(source_ini).stem + submit_suffix)
    submit_ini.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=submit_ini.parent, delete=False) as handle:
        handle.write(rewritten)
        tmp = Path(handle.name)
    tmp.replace(submit_ini)

    manifest = {
        "event": event,
        "generated_at": time.time(),
        "source_host": source_host.name,
        "target_host": target_host.name,
        "source_ini": source_ini,
        "original_ini": str(original_ini),
        "submit_ini": str(submit_ini),
        "data_subdir": data_subdir,
        "dependencies": copied,
        "path_replacements": replacements,
    }
    manifest_path = event_dir / "input_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    status_path = event_dir / "status.yaml"
    status = yaml.safe_load(status_path.read_text()) if status_path.is_file() else {}
    if not isinstance(status, dict):
        status = {}
    status.update({
        "status": status.get("status", "pending"),
        "source_host": source_host.name,
        "target_host": target_host.name,
        "source_ini": source_ini,
        "original_ini": str(original_ini),
        "submit_ini": str(submit_ini),
        "input_manifest": str(manifest_path),
    })
    status_path.write_text(yaml.safe_dump(status, sort_keys=False))
    return EventImportResult(event=event, source_ini=source_ini, original_ini=original_ini, submit_ini=submit_ini, manifest_path=manifest_path, dependencies=tuple(copied))


def import_events(
    *,
    hosts_file: Path,
    source_host_name: str,
    target_host_name: str,
    source_dir: str,
    target_project_dir: Path | None,
    apx: str,
    events: list[str] | None = None,
    data_subdir: str = "data",
    submit_suffix: str = ".target.ini",
    preserve_roots: list[str] | None = None,
    rsync_args: list[str] | None = None,
) -> dict[str, Any]:
    profiles = HostProfiles.load(hosts_file)
    source_host = profiles[source_host_name]
    target_host = profiles[target_host_name]
    target_project = target_project_dir or target_host.require_project_dir()
    discovery = discover_remote_configs(source_host, source_dir, apx, events=events)
    results = []
    for event, info in sorted(discovery.get("events", {}).items()):
        result = materialize_event(
            event=event,
            source_ini=info["source_ini"],
            source_host=source_host,
            target_host=target_host,
            target_project_dir=target_project,
            preserve_roots=preserve_roots,
            data_subdir=data_subdir,
            submit_suffix=submit_suffix,
            rsync_args=rsync_args,
        )
        results.append({
            "event": result.event,
            "source_ini": result.source_ini,
            "submit_ini": str(result.submit_ini),
            "manifest_path": str(result.manifest_path),
            "dependency_count": len(result.dependencies),
        })
    summary = {"generated_at": time.time(), "source_host": source_host_name, "target_host": target_host_name, "source_dir": source_dir, "target_project_dir": str(target_project), "events": results}
    imports_dir = target_project / "control" / "imports"
    imports_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    (imports_dir / f"import-{source_host_name}-to-{target_host_name}-{stamp}.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    return summary
