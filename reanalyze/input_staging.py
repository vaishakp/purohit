"""Stage local bilby input files before submission.

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
from pathlib import Path
import re
import shlex
import shutil
import tempfile
import time
from typing import Any

import yaml

STAGING_CONFIG_FILENAME = "staging.yaml"
PATH_KEY_HINTS = ("file", "path", "psd", "calibration", "envelope", "data", "dump", "prior", "injection", "roq", "basis", "weights", "lookup")
DEFAULT_PRESERVE_ROOTS = ("/cvmfs", "/archive", "/frames", "/hdfs", "/dev", "/proc", "/sys")


@dataclass(frozen=True)
class StagedConfig:
    enabled: bool
    config_path: Path
    manifest_path: Path | None = None
    copied_files: tuple[dict[str, Any], ...] = ()


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


def stage_dir(project_dir: Path, event: str, config: dict[str, Any]) -> Path:
    base = Path(config.get("local_project_dir") or project_dir).expanduser()
    event_subdir = str(config.get("event_subdir") or f"working/{event}")
    return base / event_subdir / str(config.get("stage_subdir", "staged_inputs"))


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

    parser = parse_ini_lossy(config_path)
    discovered = discover_config_paths(config_path, parser, config)
    local_stage_dir = stage_dir(project_dir, event, config)
    local_stage_dir.mkdir(parents=True, exist_ok=True)

    suffix = str(config.get("rewrite_config_suffix", ".staged.ini"))
    rewritten_name = config_path.with_suffix("").name + suffix
    rewritten_local = local_stage_dir.parent / rewritten_name
    manifest_path = local_stage_dir.parent / "input_manifest.json"

    seen_names: set[str] = set()
    replacements: dict[str, str] = {}
    manifest_files: list[dict[str, Any]] = []
    remote_prefix = str(config.get("remote_stage_prefix") or "").rstrip("/")

    for item in discovered:
        src = Path(item["path"])
        staged_name = unique_stage_name(src, seen_names)
        local_dst = local_stage_dir / staged_name
        shutil.copy2(src, local_dst)
        config_dst = str(local_dst)
        if remote_prefix:
            config_dst = f"{remote_prefix}/{staged_name}"
        for old in {str(src), str(src.expanduser()), str(src.resolve()), str(item["original"])}:
            replacements[old] = config_dst
        manifest_files.append({"section": item["section"], "key": item["key"], "source": str(src), "staged": config_dst, "local_staged": str(local_dst), "size_bytes": src.stat().st_size, "sha256": sha256_file(src) if bool(config.get("hash_files", True)) else None})

    write_rewritten_config(config_path, rewritten_local, replacements)
    manifest = {"event": event, "enabled": True, "generated_at": time.time(), "source_config": str(config_path), "rewritten_config": str(rewritten_local), "files": manifest_files, "replacements": replacements, "note": "Copy the rewritten config and staged_inputs directory to the remote submit account if remote_stage_prefix is set."}
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return StagedConfig(enabled=True, config_path=rewritten_local, manifest_path=manifest_path, copied_files=tuple(manifest_files))
