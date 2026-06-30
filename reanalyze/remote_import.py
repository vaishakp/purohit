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
import getpass
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
    ini_path: str
    kind: str


@dataclass(frozen=True)
class EventImportResult:
    event: str
    source_ini: str
    original_ini: Path
    submit_ini: Path
    manifest_path: Path
    dependencies: tuple[dict[str, Any], ...]


def _log(message: str, *, verbose: bool = True) -> None:
    if verbose:
        print(f"[purohit remote-import] {message}", flush=True)


def _format_command(command: list[str]) -> str:
    return " ".join(shlex.quote(str(part)) for part in command)


def run_checked(command: list[str], *, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(command, input=input_text, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            "Command failed\n"
            f"  command: {_format_command(command)}\n"
            f"  returncode: {proc.returncode}\n"
            f"  stdout:\n{proc.stdout or '<empty>'}\n"
            f"  stderr:\n{proc.stderr or '<empty>'}"
        )
    return proc


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
    remote_command = " ".join(shlex.quote(str(part)) for part in ["python3", "-", *args])
    command = ["ssh", source_host.require_ssh(), remote_command]
    out = run_checked(command, input_text=code)
    return out.stdout


REMOTE_DISCOVERY_CODE = r'''
from __future__ import annotations
import json, os, re, sys
from pathlib import Path
source_dir = Path(sys.argv[1]).expanduser()
apx = sys.argv[2].lower()
events = set(item for item in sys.argv[3].split(',') if item) if len(sys.argv) > 3 else set()
approvals = json.loads(sys.argv[4]) if len(sys.argv) > 4 and sys.argv[4] else {}
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

grouped = {}
for event, path in sorted(files):
    grouped.setdefault(event, []).append(path)
selected = {}
for event, event_files in sorted(grouped.items()):
    if event in approvals:
        token = str(approvals[event])
        matches = [path for path in event_files if token in path]
        selected[event] = matches[0] if matches else event_files[-1]
    else:
        selected[event] = event_files[0]
print(json.dumps({'source_dir': str(source_dir), 'events': {event: {'source_ini': path} for event, path in selected.items()}}, sort_keys=True))
'''


def discover_remote_configs(
    source_host: HostProfile,
    source_dir: str,
    apx: str,
    events: list[str] | None = None,
    approvals: dict[str, str] | None = None,
    *,
    verbose: bool = True,
) -> dict[str, Any]:
    event_note = f"{len(events)} requested event(s)" if events else "all matching events"
    approval_note = f"{len(approvals or {})} approval token(s)"
    _log(
        f"discovering configs on {source_host.name} via {source_host.require_ssh()} "
        f"under {source_dir!r} for apx={apx!r} ({event_note}, {approval_note})",
        verbose=verbose,
    )
    start = time.time()
    stdout = run_remote_python(source_host, REMOTE_DISCOVERY_CODE, [source_dir, apx, ",".join(events or []), json.dumps(approvals or {})])
    discovery = json.loads(stdout)
    n_events = len(discovery.get("events", {}))
    _log(f"discovery complete: selected {n_events} event(s) in {time.time() - start:.1f} s", verbose=verbose)
    return discovery


def parse_ini_dependencies_text(
    text: str,
    *,
    preserve_roots: list[str] | None = None,
    path_key_hints: tuple[str, ...] = PATH_KEY_HINTS,
    base_dir: str | None = None,
) -> list[Dependency]:
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
        for candidate in split_dependency_candidates(value.strip()):
            ini_path = candidate.strip(",}{ ")
            expanded = os.path.expandvars(os.path.expanduser(ini_path))
            if not is_probable_path(expanded): # require / in candidate paths
                continue
            if expanded.startswith("/"):
                source_path = posixpath.normpath(expanded)
            elif base_dir:
                source_path = posixpath.normpath(posixpath.join(base_dir, expanded))
            else:
                continue
            if any(source_path == root or source_path.startswith(root.rstrip("/") + "/") for root in preserve):
                continue
            kind = classify_dependency_key(key)
            deps.append(Dependency(key=key, source_path=source_path, ini_path=ini_path, kind=kind))
    return deps


def split_dependency_candidates(value: str) -> list[str]:
    candidates: list[str] = []
    for candidate in split_pathlike_value(value):
        candidates.extend(split_detector_path_map(candidate))
    return candidates


def split_detector_path_map(value: str) -> list[str]:
    stripped = value.strip()
    if "," not in stripped or ":" not in stripped:
        return [value]

    paths: list[str] = []
    for item in stripped.split(","):
        item = item.strip()
        if not item:
            continue
        detector, sep, path = item.partition(":")
        if not sep or not detector.strip() or not path.strip():
            return [value]
        detector_name = detector.strip()
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", detector_name):
            return [value]
        paths.append(path.strip())
    return paths or [value]


def classify_dependency_key(key: str) -> str:
    lower = key.lower()
    for kind in ("psd", "calibration", "prior", "injection", "roq", "basis", "weights", "lookup", "data"):
        if kind in lower:
            return kind
    return "input"


def is_probable_relative_file(value: str) -> bool:
    name = PurePosixPath(value).name
    return bool(name and "." in name and not any(part in {"", ".", ".."} for part in PurePosixPath(value).parts))


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


def set_ini_value(text: str, key: str, value: str) -> str:
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=.*$", re.MULTILINE)
    replacement = f"{key}={value}"
    if pattern.search(text):
        return pattern.sub(replacement, text)
    suffix = "" if text.endswith("\n") else "\n"
    return f"{text}{suffix}{replacement}\n"


def remove_ini_key(text: str, key: str) -> str:
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=.*(?:\n|$)", re.MULTILINE)
    return pattern.sub("", text)


def active_conda_env_name() -> str | None:
    env_name = os.environ.get("CONDA_DEFAULT_ENV")
    if env_name:
        return env_name
    conda_prefix = os.environ.get("CONDA_PREFIX")
    if conda_prefix:
        return Path(conda_prefix).name
    return None


def rewrite_default_spin_priors(text: str) -> str:
    text = re.sub(
        r"a_1\s*=\s*Uniform\s*\(\s*name\s*=\s*'a_1',\s*minimum\s*=\s*0,\s*maximum\s*=\s*0\.99\s*\)",
        "a_1 = PowerLaw(name='a_1', minimum=0, maximum=1, alpha=2)",
        text,
    )
    text = re.sub(
        r"\s*a_2\s*=\s*Uniform\s*\(\s*name\s*=\s*'a_2',\s*minimum\s*=\s*0,\s*maximum\s*=\s*0\.99\s*\)",
        "a_2 = PowerLaw(name='a_2', minimum=0, maximum=1, alpha=2)",
        text,
    )
    return text


def reconfigure_submit_ini_text(
    text: str,
    *,
    event: str,
    submit_ini: Path,
    target_project_dir: Path,
    apx: str,
    accounting: str | None = "ligo.dev.o4.cbc.pe.bilby",
    accounting_user: str = "auto",
    label_suffix: str = "_p2",
    preserve_osg_settings: bool = False,
) -> str:
    analysis_executable = shutil.which("bilby_pipe_analysis")
    if analysis_executable is None:
        raise FileNotFoundError("Could not find 'bilby_pipe' on PATH. Activate the intended bilby_pipe environment first.")

    conda_env = active_conda_env_name()
    resolved_accounting_user = getpass.getuser() if accounting_user == "auto" else accounting_user
    outdir_path = str(submit_ini.resolve().parent / "pe").replace("/home/ligo/", "/scratch2/")
    updates = {
        "label": f"{event}{label_suffix}",
        "outdir": outdir_path,
        "webdir": str(target_project_dir / "webdir"),
        "accounting": accounting,
        "accounting-user": resolved_accounting_user,
        "request-memory": "8",
        "request-disk": "16",
        "request-cpus": "16",
        "analysis-executable": analysis_executable,
        "conda-env": "None",
        "submit": "False",
        "queue": "None",
        "transfer-files": "True",
        "scitoken-issuer": "igwn",
        "sampler-kwargs": "{'nlive': 2000, 'naccept': 60, 'check_point_plot': True, 'check_point_delta_t': 1800, 'print_method': 'interval-60', 'sample': 'acceptance-walk', 'npool': 16, 'dlogz': 0.01}",
    }
    if not preserve_osg_settings:
        updates.update({"osg": "False", "transfer-files": "False", "scheduler-env": "None"})
    if apx == "NRSur7dq4":
        updates["additional-transfer-paths"] = "[/scratch/lalsimulation/NRSur7dq4_v1.0.h5]"
    if "data-dict=None" in text:
        updates.update({"transfer-files": "True"})

    rewritten = text
    for key, value in updates.items():
        if value is not None:
            rewritten = set_ini_value(rewritten, key, str(value))
    if conda_env is None:
        rewritten = remove_ini_key(rewritten, "conda-env")
    if not preserve_osg_settings:
        rewritten = remove_ini_key(rewritten, "container")
    return rewrite_default_spin_priors(rewritten)


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
    apx: str = "",
    accounting: str | None = "ligo.dev.o4.cbc.pe.bilby",
    accounting_user: str = "auto",
    label_suffix: str = "_p2",
    preserve_osg_settings: bool = False,
    verbose: bool = True,
) -> EventImportResult:
    event_dir = target_project_dir / "working" / event
    original_dir = event_dir / "original"
    original_dir.mkdir(parents=True, exist_ok=True)
    original_ini = original_dir / (Path(source_ini).name.replace(".ini", ".source.ini"))
    _log(f"{event}: copying source INI {source_ini} -> {original_ini}", verbose=verbose)
    rsync_pull(source_host, source_ini, original_ini, rsync_args=rsync_args)

    ini_text = original_ini.read_text()
    dependencies = parse_ini_dependencies_text(ini_text, preserve_roots=preserve_roots, base_dir=posixpath.dirname(source_ini))
    _log(f"{event}: found {len(dependencies)} dependency file(s) to stage", verbose=verbose)
    replacements: dict[str, str] = {}
    copied: list[dict[str, Any]] = []
    for index, dep in enumerate(dependencies, start=1):
        target_path = event_data_path(target_project_dir, event, dep.source_path, source_host.require_home(), data_subdir=data_subdir)
        _log(f"{event}: staging dependency {index}/{len(dependencies)} [{dep.kind}] {dep.source_path}", verbose=verbose)
        rsync_pull(source_host, dep.source_path, target_path, rsync_args=rsync_args)
        replacements[dep.source_path] = str(target_path)
        if dep.ini_path != dep.source_path:
            replacements[dep.ini_path] = str(target_path)
        copied.append({
            "key": dep.key,
            "kind": dep.kind,
            "source_path": dep.source_path,
            "ini_path": dep.ini_path,
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
    rewritten = reconfigure_submit_ini_text(
        rewritten,
        event=event,
        submit_ini=submit_ini,
        target_project_dir=target_project_dir,
        apx=apx,
        accounting=accounting,
        accounting_user=accounting_user,
        label_suffix=label_suffix,
        preserve_osg_settings=preserve_osg_settings,
    )
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
        "preserve_osg_settings": preserve_osg_settings,
        "apx": apx,
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
        "preserve_osg_settings": preserve_osg_settings,
        "apx": apx,
    })
    status_path.write_text(yaml.safe_dump(status, sort_keys=False))
    _log(f"{event}: wrote submit INI {submit_ini} and manifest {manifest_path}", verbose=verbose)
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
    approvals: dict[str, str] | None = None,
    data_subdir: str = "data",
    submit_suffix: str = ".target.ini",
    preserve_roots: list[str] | None = None,
    rsync_args: list[str] | None = None,
    accounting: str | None = "ligo.dev.o4.cbc.pe.bilby",
    accounting_user: str = "auto",
    label_suffix: str = "_p2",
    preserve_osg_settings: bool = False,
    verbose: bool = True,
) -> dict[str, Any]:
    profiles = HostProfiles.load(hosts_file)
    source_host = profiles[source_host_name]
    target_host = profiles[target_host_name]
    target_project = target_project_dir or target_host.require_project_dir()
    _log(
        f"starting import: source={source_host_name}, target={target_host_name}, "
        f"target_project={target_project}",
        verbose=verbose,
    )
    discovery = discover_remote_configs(source_host, source_dir, apx, events=events, approvals=approvals, verbose=verbose)
    selected_events = sorted(discovery.get("events", {}).items())
    _log(f"materializing {len(selected_events)} selected event(s)", verbose=verbose)
    results = []
    for index, (event, info) in enumerate(selected_events, start=1):
        _log(f"event {index}/{len(selected_events)}: {event}", verbose=verbose)
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
            apx=apx,
            accounting=accounting,
            accounting_user=accounting_user,
            label_suffix=label_suffix,
            preserve_osg_settings=preserve_osg_settings,
            verbose=verbose,
        )
        results.append({
            "event": result.event,
            "source_ini": result.source_ini,
            "submit_ini": str(result.submit_ini),
            "manifest_path": str(result.manifest_path),
            "dependency_count": len(result.dependencies),
        })
    summary = {
        "generated_at": time.time(),
        "source_host": source_host_name,
        "target_host": target_host_name,
        "source_dir": source_dir,
        "target_project_dir": str(target_project),
        "approval_events": sorted((approvals or {}).keys()),
        "preserve_osg_settings": preserve_osg_settings,
        "accounting": accounting,
        "accounting_user": getpass.getuser() if accounting_user == "auto" else accounting_user,
        "label_suffix": label_suffix,
        "events": results,
    }
    imports_dir = target_project / "control" / "imports"
    imports_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    summary_path = imports_dir / f"import-{source_host_name}-to-{target_host_name}-{stamp}.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    _log(f"import complete: wrote summary {summary_path}", verbose=verbose)
    return summary
