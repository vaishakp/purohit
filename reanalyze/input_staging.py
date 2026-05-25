"""Stage local bilby input files before submission.

This module is intentionally conservative: staging is disabled unless
``project_dir/control/staging.yaml`` sets ``enabled: true``. When enabled, the
module scans the event INI for existing local file paths, copies or rsyncs those
files into a per-event staging area, writes a rewritten INI, and returns the
command that should be used for submission.
"""

from __future__ import annotations

from dataclasses import dataclass
import ast
import hashlib
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import time
from typing import Any

import yaml

STAGING_CONFIG = "staging.yaml"
DEFAULT_STAGE_SUBDIR = "staged_inputs"
DEFAULT_REWRITE_SUFFIX = ".staged.ini"


@dataclass(frozen=True)
class StagingResult:
    enabled: bool
    mode: str
    config_path: Path
    submit_command: list[str]
    manifest_path: Path | None = None
    staged_config_path: Path | None = None
    remote_config_path: str | None = None
    staged_files: list[dict[str, Any]] | None = None


def staging_config_path(project_dir: Path) -> Path:
    return project_dir / "control" / STAGING_CONFIG


def read_staging_config(project_dir: Path) -> dict[str, Any]:
    path = staging_config_path(project_dir)
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text()) or {}
    return data if isinstance(data, dict) else {}


def staging_enabled(project_dir: Path) -> bool:
    return bool(read_staging_config(project_dir).get("enabled", False))


def _as_path_list(values: Any) -> list[Path]:
    if values is None:
        return []
    if isinstance(values, (str, Path)):
        values = [values]
    return [Path(str(item)).expanduser().resolve() for item in values]


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _preserved(path: Path, preserve_roots: list[Path]) -> bool:
    return any(_is_under(path, root) for root in preserve_roots)


def _allowed_by_copy_roots(path: Path, copy_roots: list[Path]) -> bool:
    return not copy_roots or any(_is_under(path, root) for root in copy_roots)


def _literal_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        out: list[str] = []
        for item in value.values():
            out.extend(_literal_strings(item))
        return out
    if isinstance(value, (list, tuple, set)):
        out: list[str] = []
        for item in value:
            out.extend(_literal_strings(item))
        return out
    return []


def _strings_from_value(raw: str) -> list[str]:
    text = raw.strip()
    if not text or text.lower() in {"none", "true", "false"}:
        return []
    candidates = [text]
    try:
        parsed = ast.literal_eval(text)
    except Exception:  # noqa: BLE001 - config values are intentionally loose
        parsed = None
    if parsed is not None:
        candidates.extend(_literal_strings(parsed))
    # Also catch paths inside unquoted dict-like strings.
    candidates.extend(re.findall(r"(/[^\s,\]})'\"]+)", text))
    out: list[str] = []
    for item in candidates:
        item = str(item).strip().strip("'\"")
        if item and item not in out:
            out.append(item)
    return out


def scan_config_paths(config_path: Path, cfg: dict[str, Any]) -> list[Path]:
    preserve_roots = _as_path_list(cfg.get("preserve_roots", ["/cvmfs"]))
    copy_roots = _as_path_list(cfg.get("copy_roots"))
    discovered: list[Path] = []
    for line in config_path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", ";")) or "=" not in line:
            continue
        _, raw_value = line.split("=", 1)
        for value in _strings_from_value(raw_value):
            if not value.startswith("/"):
                continue
            path = Path(value).expanduser()
            try:
                resolved = path.resolve(strict=False)
            except OSError:
                resolved = path
            if not path.exists() or not path.is_file():
                continue
            if _preserved(resolved, preserve_roots):
                continue
            if not _allowed_by_copy_roots(resolved, copy_roots):
                continue
            if resolved not in discovered:
                discovered.append(resolved)
    return discovered


def sha256_file(path: Path, block_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(block_size)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def staged_relative_path(path: Path, copy_roots: list[Path]) -> Path:
    resolved = path.resolve()
    for index, root in enumerate(copy_roots):
        if _is_under(resolved, root):
            return Path(f"root{index}") / resolved.relative_to(root)
    return Path("abs") / Path(*resolved.parts[1:])


def run_checked(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=True, capture_output=True, text=True)


def rsync_file(source: Path, target_host: str, remote_path: str, rsync_args: list[str]) -> None:
    remote_parent = str(Path(remote_path).parent)
    run_checked(["ssh", target_host, "mkdir", "-p", remote_parent])
    run_checked(["rsync", *rsync_args, str(source), f"{target_host}:{remote_path}"])


def rewrite_config_text(text: str, replacements: dict[str, str]) -> str:
    # Replace longer strings first to avoid nested-prefix replacements.
    for source in sorted(replacements, key=len, reverse=True):
        text = text.replace(source, replacements[source])
    return text


def _remote_submit_command(host: str, remote_config: str, cfg: dict[str, Any]) -> list[str]:
    preamble = str(cfg.get("remote_submit_preamble", "")).strip()
    bilby_pipe = str(cfg.get("remote_bilby_pipe", "bilby_pipe"))
    cmd = f"{shlex.quote(bilby_pipe)} {shlex.quote(remote_config)} --submit"
    if preamble:
        cmd = f"{preamble}\n{cmd}"
    return ["ssh", host, "bash", "-lc", cmd]


def stage_event_inputs(project_dir: Path, event: str, config_path: Path) -> StagingResult:
    cfg = read_staging_config(project_dir)
    if not cfg.get("enabled", False):
        return StagingResult(False, "disabled", config_path, ["bilby_pipe", str(config_path), "--submit"])

    mode = str(cfg.get("mode", "local")).lower()
    if mode not in {"local", "rsync"}:
        raise ValueError("staging mode must be 'local' or 'rsync'")

    event_dir = project_dir / "working" / event
    stage_subdir = str(cfg.get("stage_subdir", DEFAULT_STAGE_SUBDIR))
    local_stage_dir = event_dir / stage_subdir
    local_stage_dir.mkdir(parents=True, exist_ok=True)

    copy_roots = _as_path_list(cfg.get("copy_roots"))
    paths = scan_config_paths(config_path, cfg)
    replacements: dict[str, str] = {}
    staged_files: list[dict[str, Any]] = []

    remote_project_dir = cfg.get("remote_project_dir")
    target_host = cfg.get("target_host")
    remote_event_dir = None
    remote_stage_dir = None
    if mode == "rsync":
        if not target_host or not remote_project_dir:
            raise ValueError("rsync staging requires target_host and remote_project_dir in control/staging.yaml")
        remote_event_dir = str(Path(str(remote_project_dir)) / "working" / event)
        remote_stage_dir = str(Path(remote_event_dir) / stage_subdir)
        run_checked(["ssh", str(target_host), "mkdir", "-p", remote_stage_dir])

    rsync_args = [str(item) for item in cfg.get("rsync_args", ["-a", "--partial", "--protect-args"])]

    for source in paths:
        rel = staged_relative_path(source, copy_roots)
        local_dest = local_stage_dir / rel
        local_dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, local_dest)
        staged_path_for_config = str(local_dest)
        remote_dest = None
        if mode == "rsync":
            remote_dest = str(Path(str(remote_stage_dir)) / rel.as_posix())
            rsync_file(local_dest, str(target_host), remote_dest, rsync_args)
            staged_path_for_config = remote_dest
        replacements[str(source)] = staged_path_for_config
        staged_files.append({
            "source": str(source),
            "local_staged": str(local_dest),
            "remote_staged": remote_dest,
            "config_path": staged_path_for_config,
            "size_bytes": source.stat().st_size,
            "sha256": sha256_file(source),
        })

    suffix = str(cfg.get("rewrite_config_suffix", DEFAULT_REWRITE_SUFFIX))
    rewritten_config = event_dir / f"{config_path.stem}{suffix}"
    rewritten_config.write_text(rewrite_config_text(config_path.read_text(), replacements))

    remote_config = None
    submit_command = ["bilby_pipe", str(rewritten_config), "--submit"]
    if mode == "rsync":
        remote_config = str(Path(str(remote_event_dir)) / rewritten_config.name)
        rsync_file(rewritten_config, str(target_host), remote_config, rsync_args)
        submit_command = _remote_submit_command(str(target_host), remote_config, cfg)

    manifest = {
        "generated_at": time.time(),
        "event": event,
        "mode": mode,
        "source_config": str(config_path),
        "staged_config": str(rewritten_config),
        "remote_config": remote_config,
        "target_host": target_host,
        "remote_project_dir": remote_project_dir,
        "files": staged_files,
        "submit_command": submit_command,
    }
    manifest_path = event_dir / "input_staging_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    if mode == "rsync":
        remote_manifest = str(Path(str(remote_event_dir)) / manifest_path.name)
        rsync_file(manifest_path, str(target_host), remote_manifest, rsync_args)

    return StagingResult(True, mode, rewritten_config, submit_command, manifest_path, rewritten_config, remote_config, staged_files)
