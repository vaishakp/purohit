"""Stage bilby input files for submission from another account/host.

The staging layer is intentionally conservative. It scans an event INI for
absolute paths, copies only existing regular files that are allowed by
``control/staging.yaml``, rewrites the INI to point at the staged files, and
returns the command that should be used for submission.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
import time
from typing import Any

import yaml

DEFAULT_STAGING_FILENAME = "staging.yaml"
PATH_RE = re.compile(r"(?<![A-Za-z0-9_.-])(/[^\s'\";,\])}]+)")


@dataclass(frozen=True)
class StagedInput:
    source: str
    staged: str
    local_staged: str | None
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class StagingResult:
    enabled: bool
    config_path: Path
    submit_command: list[str]
    manifest_path: Path | None = None
    remote_config_path: str | None = None
    manifest: dict[str, Any] | None = None


def default_staging_path(project_dir: Path) -> Path:
    return project_dir / "control" / DEFAULT_STAGING_FILENAME


def read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    value = yaml.safe_load(path.read_text()) or {}
    return value if isinstance(value, dict) else {}


def staging_config(project_dir: Path, staging_file: Path | None = None) -> dict[str, Any]:
    return read_yaml((staging_file or default_staging_path(project_dir)).expanduser())


def staging_enabled(project_dir: Path, staging_file: Path | None = None) -> bool:
    return bool(staging_config(project_dir, staging_file).get("enabled", False))


def _as_path_list(values: Any) -> list[Path]:
    if values is None:
        return []
    if isinstance(values, (str, Path)):
        values = [values]
    return [Path(str(item)).expanduser().resolve() for item in values]


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _preserved(path: Path, preserve_roots: list[Path]) -> bool:
    return any(_is_relative_to(path, root) for root in preserve_roots)


def _allowed(path: Path, copy_roots: list[Path]) -> bool:
    if not copy_roots:
        return True
    return any(_is_relative_to(path, root) for root in copy_roots)


def _sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _safe_name(source: Path, used: set[str]) -> str:
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", source.name).strip("._") or "input"
    digest = hashlib.sha1(str(source).encode("utf-8")).hexdigest()[:10]
    candidate = f"{digest}_{stem}"
    while candidate in used:
        digest = hashlib.sha1((candidate + str(source)).encode("utf-8")).hexdigest()[:10]
        candidate = f"{digest}_{stem}"
    used.add(candidate)
    return candidate


def discover_input_paths(config_text: str, *, copy_roots: list[Path], preserve_roots: list[Path], max_file_size_mb: float | None = None) -> list[Path]:
    paths: list[Path] = []
    seen: set[Path] = set()
    max_bytes = None if max_file_size_mb is None else int(max_file_size_mb * 1024 * 1024)
    for match in PATH_RE.finditer(config_text):
        raw = match.group(1).rstrip(".,")
        path = Path(raw).expanduser()
        if not path.is_absolute():
            continue
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        if _preserved(resolved, preserve_roots):
            continue
        if not _allowed(resolved, copy_roots):
            continue
        if not resolved.is_file():
            continue
        if max_bytes is not None and resolved.stat().st_size > max_bytes:
            continue
        seen.add(resolved)
        paths.append(resolved)
    return paths


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False) as handle:
        handle.write(text)
        tmp = Path(handle.name)
    tmp.replace(path)


def atomic_write_json(path: Path, data: Any) -> None:
    atomic_write_text(path, json.dumps(data, indent=2, sort_keys=True, allow_nan=False) + "\n")


def run_checked(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=True, capture_output=True, text=True)


def shell_join(command: list[str]) -> str:
    return " ".join(shlex.quote(str(part)) for part in command)


def rsync_to_remote(local_path: Path, target_host: str, remote_path: str, rsync_args: list[str]) -> None:
    remote_spec = f"{target_host}:{remote_path}"
    run_checked(["rsync", *rsync_args, str(local_path), remote_spec])


def remote_mkdir(target_host: str, remote_dir: str) -> None:
    run_checked(["ssh", target_host, "mkdir", "-p", remote_dir])


def replace_paths(config_text: str, replacements: dict[str, str]) -> str:
    # Longest first avoids partial replacement if one path is a prefix of another.
    out = config_text
    for src in sorted(replacements, key=len, reverse=True):
        out = out.replace(src, replacements[src])
    return out


def prepare_staged_submission(project_dir: Path, event: str, config_path: Path, *, staging_file: Path | None = None) -> StagingResult:
    """Stage inputs and return the bilby submission command.

    If staging is disabled or no config file exists, the returned command is the
    ordinary local ``bilby_pipe <config> --submit`` command.
    """

    config = staging_config(project_dir, staging_file)
    if not config.get("enabled", False):
        return StagingResult(enabled=False, config_path=config_path, submit_command=["bilby_pipe", str(config_path), "--submit"])

    mode = str(config.get("mode", "local")).lower()
    if mode not in {"local", "rsync"}:
        raise ValueError("staging mode must be 'local' or 'rsync'")

    event_directory = project_dir / "working" / event
    stage_subdir = str(config.get("stage_subdir", "staged_inputs"))
    local_stage_dir = event_directory / stage_subdir
    local_stage_dir.mkdir(parents=True, exist_ok=True)

    copy_roots = _as_path_list(config.get("copy_roots"))
    preserve_roots = _as_path_list(config.get("preserve_roots", ["/cvmfs"]))
    max_file_size_mb = config.get("max_file_size_mb")
    max_file_size_mb = None if max_file_size_mb in (None, "") else float(max_file_size_mb)

    original_text = config_path.read_text()
    inputs = discover_input_paths(original_text, copy_roots=copy_roots, preserve_roots=preserve_roots, max_file_size_mb=max_file_size_mb)

    target_host = config.get("target_host")
    remote_project_dir = config.get("remote_project_dir")
    rsync_args = [str(item) for item in config.get("rsync_args", ["-a", "--partial", "--protect-args"])]
    submit_via_ssh = bool(config.get("submit_via_ssh", mode == "rsync"))
    remote_bilby_pipe = str(config.get("remote_bilby_pipe", "bilby_pipe"))

    if mode == "rsync":
        if not target_host or not remote_project_dir:
            raise ValueError("rsync staging requires target_host and remote_project_dir")
        remote_event_dir = f"{str(remote_project_dir).rstrip('/')}/working/{event}"
        remote_stage_dir = f"{remote_event_dir}/{stage_subdir}"
        remote_mkdir(str(target_host), remote_stage_dir)
    else:
        remote_event_dir = None
        remote_stage_dir = None

    used_names: set[str] = set()
    replacements: dict[str, str] = {}
    staged_inputs: list[dict[str, Any]] = []
    for source in inputs:
        name = _safe_name(source, used_names)
        local_dest = local_stage_dir / name
        shutil.copy2(source, local_dest)
        staged_path = str(local_dest)
        if mode == "rsync":
            assert remote_stage_dir is not None and target_host is not None
            remote_dest = f"{remote_stage_dir}/{name}"
            rsync_to_remote(local_dest, str(target_host), remote_dest, rsync_args)
            staged_path = remote_dest
        replacements[str(source)] = staged_path
        staged_inputs.append({
            "source": str(source),
            "staged": staged_path,
            "local_staged": str(local_dest),
            "size_bytes": source.stat().st_size,
            "sha256": _sha256(source),
        })

    suffix = str(config.get("rewrite_config_suffix", ".staged.ini"))
    rewritten_config = local_stage_dir / f"{config_path.stem}{suffix}"
    atomic_write_text(rewritten_config, replace_paths(original_text, replacements))

    remote_config_path = None
    if mode == "rsync":
        assert target_host is not None and remote_event_dir is not None
        remote_config_path = f"{remote_event_dir}/{rewritten_config.name}"
        rsync_to_remote(rewritten_config, str(target_host), remote_config_path, rsync_args)
        if submit_via_ssh:
            remote_cmd = shell_join([remote_bilby_pipe, remote_config_path, "--submit"])
            submit_command = ["ssh", str(target_host), remote_cmd]
        else:
            submit_command = ["bilby_pipe", remote_config_path, "--submit"]
    else:
        submit_command = ["bilby_pipe", str(rewritten_config), "--submit"]

    manifest = {
        "event": event,
        "generated_at": time.time(),
        "mode": mode,
        "target_host": target_host,
        "remote_project_dir": remote_project_dir,
        "remote_config_path": remote_config_path,
        "local_config_path": str(rewritten_config),
        "source_config_path": str(config_path),
        "submit_command": submit_command,
        "files": staged_inputs,
        "preserve_roots": [str(path) for path in preserve_roots],
        "copy_roots": [str(path) for path in copy_roots],
    }
    manifest_path = event_directory / "input_manifest.json"
    atomic_write_json(manifest_path, manifest)
    if mode == "rsync" and target_host and remote_event_dir:
        rsync_to_remote(manifest_path, str(target_host), f"{remote_event_dir}/input_manifest.json", rsync_args)

    return StagingResult(enabled=True, config_path=rewritten_config, remote_config_path=remote_config_path, submit_command=submit_command, manifest_path=manifest_path, manifest=manifest)
