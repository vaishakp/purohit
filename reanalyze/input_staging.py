"""Stage and optionally transfer local bilby input files before submission.

Configured by ``project_dir/control/staging.yaml``. If absent or disabled,
submission behavior is unchanged.
"""

from __future__ import annotations

from configparser import ConfigParser
from dataclasses import dataclass
import ast
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import socket
import subprocess
import tempfile
import time
from typing import Any

import yaml

STAGING_CONFIG_FILENAME = "staging.yaml"
PATH_KEY_HINTS = ("file", "path", "psd", "calibration", "envelope", "data", "dump", "prior", "injection", "roq", "basis", "weights", "lookup")
DEFAULT_PRESERVE_ROOTS = ("/cvmfs", "/archive", "/frames", "/hdfs", "/dev", "/proc", "/sys")
UNKNOWN_HOSTS = {"", "localhost", "localhost.localdomain", "unknown"}


@dataclass(frozen=True)
class StagedConfig:
    enabled: bool
    config_path: Path
    manifest_path: Path | None = None
    copied_files: tuple[dict[str, Any], ...] = ()
    transfer_enabled: bool = False
    transfer_target: str | None = None
    warning: str | None = None


def load_staging_config(project_dir: Path) -> dict[str, Any]:
    path = project_dir / "control" / STAGING_CONFIG_FILENAME
    if not path.is_file():
        return {"enabled": False}
    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict):
        raise ValueError(f"staging config must be a mapping: {path}")
    data.setdefault("enabled", False)
    return data


def parse_ini_lossy(path: Path) -> ConfigParser:
    parser = ConfigParser(interpolation=None, strict=False, delimiters=("=", ":"), inline_comment_prefixes=(";",))
    parser.optionxform = str
    text = path.read_text()
    if not re.match(r"\s*\[", text):
        text = "[DEFAULT]\n" + text
    parser.read_string(text)
    return parser


def split_pathlike_value(value: str) -> list[str]:
    raw = value.strip()
    if not raw or raw.lower() in {"none", "false", "true"}:
        return []
    try:
        parsed = ast.literal_eval(raw)
    except Exception:
        parsed = None
    if isinstance(parsed, str):
        return [parsed]
    if isinstance(parsed, (list, tuple, set)):
        return [str(item) for item in parsed if isinstance(item, (str, Path))]
    if isinstance(parsed, dict):
        return [str(item) for item in parsed.values() if isinstance(item, (str, Path))]
    try:
        return shlex.split(raw)
    except ValueError:
        return [raw]


def is_probable_path(value: str) -> bool:
    if "://" in value:
        return False
    return value.startswith(("/", "./", "../", "~", "$")) or "/" in value


def preserved_path(path: Path, preserve_roots: list[str]) -> bool:
    raw = str(path)
    return any(raw == root or raw.startswith(root.rstrip("/") + "/") for root in preserve_roots)


def allowed_by_roots(path: Path, roots: list[str]) -> bool:
    if not roots:
        return True
    resolved = path.expanduser().resolve()
    for root in roots:
        r = Path(root).expanduser().resolve()
        if resolved == r or r in resolved.parents:
            return True
    return False


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def unique_stage_name(path: Path, seen: set[str]) -> str:
    if path.name not in seen:
        seen.add(path.name)
        return path.name
    digest = hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:10]
    candidate = f"{path.stem}-{digest}{path.suffix}"
    seen.add(candidate)
    return candidate


def event_subdir(config: dict[str, Any], event: str) -> str:
    return str(config.get("event_subdir") or f"working/{event}").format(event=event).strip("/")


def local_event_dir(project_dir: Path, event: str, config: dict[str, Any]) -> Path:
    base = Path(config.get("local_project_dir") or project_dir).expanduser()
    return base / event_subdir(config, event)


def local_stage_dir(project_dir: Path, event: str, config: dict[str, Any]) -> Path:
    return local_event_dir(project_dir, event, config) / str(config.get("stage_subdir", "staged_inputs"))


def remote_event_dir(config: dict[str, Any], event: str) -> str:
    remote_project = str(config.get("remote_project_dir") or "").rstrip("/")
    if not remote_project:
        raise ValueError("remote_project_dir is required when transfer is enabled")
    return f"{remote_project}/{event_subdir(config, event)}"


def remote_stage_dir(config: dict[str, Any], event: str) -> str:
    return f"{remote_event_dir(config, event)}/{str(config.get('stage_subdir', 'staged_inputs')).strip('/')}"


def detect_hostname(config: dict[str, Any]) -> str:
    override = config.get("hostname_override")
    if override:
        return str(override)
    hostname = socket.getfqdn() or socket.gethostname()
    hostname = hostname.strip().lower()
    if hostname in UNKNOWN_HOSTS:
        raise RuntimeError("Unable to determine hostname; refusing automatic transfer/submission. Set hostname_override or transfer_enabled explicitly.")
    return hostname


def truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "always"}


def should_transfer(config: dict[str, Any]) -> tuple[bool, str, bool]:
    mode = str(config.get("transfer_enabled", "auto")).strip().lower()
    if mode in {"0", "false", "no", "off", "never"}:
        hostname = "transfer-disabled"
        return False, hostname, False
    hostname = detect_hostname(config)
    cit_marker = str(config.get("cit_hostname_contains", "ligo.caltech.edu")).lower()
    is_cit = bool(cit_marker and cit_marker in hostname)
    if mode in {"1", "true", "yes", "on", "always"}:
        return True, hostname, is_cit
    if mode != "auto":
        raise ValueError("transfer_enabled must be auto, true, or false")
    if not is_cit:
        return True, hostname, is_cit
    return truthy(config.get("transfer_from_cit", False)), hostname, is_cit


def run_checked(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=True, capture_output=True, text=True)


def remote_mkdir(host: str, path: str) -> None:
    run_checked(["ssh", host, "mkdir", "-p", path])


def rsync_file(src: Path, host: str, dst: str, config: dict[str, Any]) -> None:
    args = ["rsync", *list(config.get("rsync_args", ["-a", "--partial", "--protect-args"])), str(src), f"{host}:{dst}"]
    run_checked(args)


def discover_config_paths(config_path: Path, parser: ConfigParser, config: dict[str, Any]) -> list[dict[str, Any]]:
    preserve_roots = list(config.get("preserve_roots", DEFAULT_PRESERVE_ROOTS))
    copy_roots = list(config.get("copy_roots", []))
    strict_missing = bool(config.get("strict_missing", False))
    hints = tuple(str(item).lower() for item in config.get("path_key_hints", PATH_KEY_HINTS))
    discovered: list[dict[str, Any]] = []
    base = config_path.parent
    sections = [parser.default_section, *parser.sections()]
    for section in sections:
        items = parser.defaults().items() if section == parser.default_section else parser.items(section)
        for key, value in items:
            if not any(hint in key.lower() for hint in hints):
                continue
            for candidate in split_pathlike_value(str(value)):
                expanded = os.path.expandvars(os.path.expanduser(candidate.strip()))
                if not is_probable_path(expanded):
                    continue
                path = Path(expanded)
                if not path.is_absolute():
                    path = base / path
                if preserved_path(path, preserve_roots) or not allowed_by_roots(path, copy_roots):
                    continue
                if path.is_file():
                    discovered.append({"section": section, "key": key, "original": candidate, "path": path})
                elif strict_missing:
                    raise FileNotFoundError(f"staging candidate does not exist: {path} from {section}.{key}")
    return discovered


def write_rewritten_config(original: Path, rewritten: Path, replacements: dict[str, str]) -> None:
    text = original.read_text()
    for old in sorted(replacements, key=len, reverse=True):
        text = text.replace(old, replacements[old])
    rewritten.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=rewritten.parent, delete=False) as handle:
        handle.write(text)
        tmp = Path(handle.name)
    tmp.replace(rewritten)


def stage_bilby_inputs(project_dir: Path, event: str, config_path: Path) -> StagedConfig:
    config = load_staging_config(project_dir)
    if not config.get("enabled"):
        return StagedConfig(enabled=False, config_path=config_path)

    transfer, hostname, is_cit = should_transfer(config)
    target_host = str(config.get("target_host") or "")
    if transfer and not target_host:
        raise ValueError("target_host is required when transfer is enabled")

    parser = parse_ini_lossy(config_path)
    discovered = discover_config_paths(config_path, parser, config)
    stage = local_stage_dir(project_dir, event, config)
    stage.mkdir(parents=True, exist_ok=True)

    suffix = str(config.get("rewrite_config_suffix", ".staged.ini"))
    rewritten_name = config_path.with_suffix("").name + suffix
    rewritten_local = stage.parent / rewritten_name
    manifest_path = stage.parent / "input_manifest.json"

    if transfer:
        remote_mkdir(target_host, remote_stage_dir(config, event))

    seen_names: set[str] = set()
    replacements: dict[str, str] = {}
    manifest_files: list[dict[str, Any]] = []

    for item in discovered:
        src = Path(item["path"])
        staged_name = unique_stage_name(src, seen_names)
        local_dst = stage / staged_name
        shutil.copy2(src, local_dst)
        config_dst = str(local_dst)
        remote_dst = None
        if transfer:
            remote_dst = f"{remote_stage_dir(config, event)}/{staged_name}"
            rsync_file(local_dst, target_host, remote_dst, config)
            config_dst = remote_dst
        elif config.get("remote_stage_prefix"):
            config_dst = f"{str(config['remote_stage_prefix']).rstrip('/')}/{staged_name}"
        for old in {str(src), str(src.expanduser()), str(src.resolve()), str(item["original"])}:
            replacements[old] = config_dst
        manifest_files.append({"section": item["section"], "key": item["key"], "source": str(src), "staged": config_dst, "local_staged": str(local_dst), "remote_staged": remote_dst, "size_bytes": src.stat().st_size, "sha256": sha256_file(src) if bool(config.get("hash_files", True)) else None})

    write_rewritten_config(config_path, rewritten_local, replacements)
    final_config = str(rewritten_local)
    remote_config = None
    if transfer:
        remote_config = f"{remote_event_dir(config, event)}/{rewritten_name}"
        rsync_file(rewritten_local, target_host, remote_config, config)
        final_config = remote_config

    manifest = {"event": event, "enabled": True, "generated_at": time.time(), "hostname": hostname, "is_cit_host": is_cit, "transfer_enabled": transfer, "transfer_target": target_host if transfer else None, "source_config": str(config_path), "rewritten_config": final_config, "local_rewritten_config": str(rewritten_local), "remote_rewritten_config": remote_config, "files": manifest_files, "replacements": replacements}
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    if transfer:
        rsync_file(manifest_path, target_host, f"{remote_event_dir(config, event)}/input_manifest.json", config)

    return StagedConfig(enabled=True, config_path=Path(final_config), manifest_path=manifest_path, copied_files=tuple(manifest_files), transfer_enabled=transfer, transfer_target=target_host if transfer else None)
