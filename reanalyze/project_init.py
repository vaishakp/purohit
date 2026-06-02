"""Common Purohit project initialization.

This module chooses the correct materialization path at project-creation time:

* if the current host is the configured source host, use the local ``PERerun``
  preparation path;
* otherwise, import the event configs and input dependencies from the source host
  to the current/target host using the remote-import machinery.

The intended result is that users run one command before starting the monitor.
After this command, ``project_dir/working/<event>/status.yaml`` points to a
submit-ready config that is local to the cluster where the monitor will run.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import secrets
import time
from typing import Any

import yaml

from reanalyze.host_profiles import HostProfile, HostProfiles
from reanalyze.reanalyze import PERerun
from reanalyze.remote_import import import_events


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _write_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False))


def _load_yaml_mapping(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    data = yaml.safe_load(path.expanduser().read_text()) or {}
    if not isinstance(data, dict):
        raise ValueError(f"YAML file must contain a mapping: {path}")
    return data


def _parse_key_value(items: list[str] | None, *, option: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for item in items or []:
        if "=" not in item:
            raise ValueError(f"{option} entries must have the form EVENT=TOKEN: {item!r}")
        key, value = item.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or not value:
            raise ValueError(f"{option} entries must have non-empty EVENT and TOKEN: {item!r}")
        parsed[key] = value
    return parsed


def _ensure_token(project_dir: Path, token_file: Path | None = None, *, overwrite: bool = False) -> Path:
    path = token_file or project_dir / "control" / "tunnel_token.txt"
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite:
        return path
    path.write_text(secrets.token_urlsafe(32) + "\n")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def _profile_name(profile: HostProfile | None) -> str | None:
    return None if profile is None else profile.name


def _require_profile(profiles: HostProfiles, name: str | None, role: str) -> HostProfile:
    if not name:
        raise ValueError(f"{role} host could not be determined; pass --{role}-host or add matching hostname_contains to hosts.yaml")
    return profiles[name]


def _log(message: str, *, verbose: bool = True) -> None:
    if verbose:
        print(f"[purohit init-project] {message}", flush=True)


def init_project(
    *,
    hosts_file: Path,
    source_host_name: str,
    source_dir: str | None = None,
    project_dir: Path | None = None,
    apx: str = "NRSur7dq4",
    approvals: dict[str, str] | None = None,
    overwrite_configs: bool = False,
    reconfigure_existing_configs: bool = True,
    preserve_osg_settings: bool = False,
    accounting: str | None = "ligo.dev.o4.cbc.pe.bilby",
    accounting_user: str = "auto",
    label_suffix: str = "_p2",
    cache_config_discovery: bool = True,
    refresh_config_cache: bool = False,
    config_cache_file: Path | str | None = None,
    working_dir: str | Path | None = None,
    verbose: bool = True,
    progress: bool = True,
    target_host_name: str | None = None,
    events: list[str] | None = None,
    mode: str = "auto",
    data_subdir: str = "data",
    submit_suffix: str = ".target.ini",
    preserve_roots: list[str] | None = None,
    rsync_args: list[str] | None = None,
    create_token: bool = True,
    token_file: Path | None = None,
) -> dict[str, Any]:
    if source_dir is None:
        if working_dir is None:
            raise TypeError("init_project requires source_dir=... for the source config tree")
        source_dir = str(working_dir)
    elif working_dir is not None and Path(source_dir) != Path(working_dir):
        raise ValueError("Pass only source_dir=...; working_dir=... is a deprecated PERerun alias")

    _log(f"loading host profiles from {hosts_file}", verbose=verbose)
    profiles = HostProfiles.load(hosts_file)
    current = profiles.detect_current()
    source = profiles[source_host_name]
    target_name = target_host_name or _profile_name(current)
    target = _require_profile(profiles, target_name, "target")
    target_project = (project_dir or target.require_project_dir()).expanduser()
    target_project.mkdir(parents=True, exist_ok=True)
    (target_project / "control").mkdir(parents=True, exist_ok=True)

    is_on_source = current is not None and current.name == source.name
    if mode not in {"auto", "local", "remote"}:
        raise ValueError("mode must be auto, local, or remote")
    use_local = mode == "local" or (mode == "auto" and is_on_source)
    use_remote = mode == "remote" or (mode == "auto" and not is_on_source)

    approvals = {str(key): str(value) for key, value in (approvals or {}).items()}
    _log(
        f"mode={'local' if use_local else 'remote'} current={_profile_name(current)} "
        f"source={source.name} target={target.name} target_project={target_project}",
        verbose=verbose,
    )
    _log(
        f"source_dir={source_dir} apx={apx} approvals={len(approvals)} "
        f"events_requested={len(events or []) if events else 'all'} "
        f"preserve_osg_settings={preserve_osg_settings}",
        verbose=verbose,
    )

    summary: dict[str, Any] = {
        "generated_at": time.time(),
        "mode": "local" if use_local else "remote",
        "current_host": _profile_name(current),
        "source_host": source.name,
        "target_host": target.name,
        "source_dir": source_dir,
        "target_project_dir": str(target_project),
        "apx": apx,
        "approval_events": sorted(approvals),
        "events_requested": events or [],
        "preserve_osg_settings": preserve_osg_settings,
        "pererun_arguments": {
            "overwrite_configs": overwrite_configs,
            "reconfigure_existing_configs": reconfigure_existing_configs,
            "preserve_osg_settings": preserve_osg_settings,
            "accounting": accounting,
            "accounting_user": accounting_user,
            "label_suffix": label_suffix,
            "cache_config_discovery": cache_config_discovery,
            "refresh_config_cache": refresh_config_cache,
            "config_cache_file": str(config_cache_file) if config_cache_file else None,
            "verbose": verbose,
            "progress": progress,
        },
    }

    if use_local:
        _log("preparing configs with local PERerun path", verbose=verbose)
        rerun = PERerun(
            source_dir=source_dir,
            project_dir=target_project,
            apx=apx,
            approvals=approvals,
            overwrite_configs=overwrite_configs,
            reconfigure_existing_configs=reconfigure_existing_configs,
            preserve_osg_settings=preserve_osg_settings,
            accounting=accounting,
            accounting_user=accounting_user,
            label_suffix=label_suffix,
            cache_config_discovery=cache_config_discovery,
            refresh_config_cache=refresh_config_cache,
            config_cache_file=config_cache_file,
            verbose=verbose,
            progress=progress,
        )
        _log("discovering/preparing source configs", verbose=verbose)
        rerun.prepare_configs()
        if events:
            selected = set(events)
            before = len(rerun.config_paths)
            rerun.config_paths = {event: path for event, path in rerun.config_paths.items() if event in selected}
            rerun.source_dict = {event: path for event, path in rerun.source_dict.items() if event in selected}
            _log(f"filtered local configs from {before} to {len(rerun.config_paths)} requested event(s)", verbose=verbose)
        _log("rewriting local configs for target project", verbose=verbose)
        rerun.reconfigure()
        _log("parsing submitted jobs list", verbose=verbose)
        rerun.parse_submitted_jobs_list()
        summary["events"] = [
            {"event": event, "submit_ini": str(path), "dependency_count": None}
            for event, path in sorted(rerun.config_paths.items())
        ]
    elif use_remote:
        _log("importing configs and inputs from remote source host", verbose=verbose)
        remote_summary = import_events(
            hosts_file=hosts_file,
            source_host_name=source.name,
            target_host_name=target.name,
            source_dir=source_dir,
            target_project_dir=target_project,
            apx=apx,
            events=events or None,
            approvals=approvals,
            data_subdir=data_subdir,
            submit_suffix=submit_suffix,
            preserve_roots=preserve_roots,
            rsync_args=rsync_args,
            preserve_osg_settings=preserve_osg_settings,
            verbose=verbose and progress,
        )
        summary["events"] = remote_summary.get("events", [])
        summary["remote_import"] = remote_summary
    else:  # pragma: no cover - guarded by mode logic above
        raise RuntimeError("unreachable project initialization mode")

    if create_token:
        token_path = _ensure_token(target_project, token_file)
        summary["token_file"] = str(token_path)
        _log(f"token file ready: {token_path}", verbose=verbose)

    init_summary_path = target_project / "control" / "project_init_summary.json"
    init_config_path = target_project / "control" / "project_init.yaml"
    _write_json(init_summary_path, summary)
    _write_yaml(
        init_config_path,
        {
            "hosts": str(hosts_file.expanduser()),
            "source_host": source.name,
            "target_host": target.name,
            "source_dir": source_dir,
            "project_dir": str(target_project),
            "apx": apx,
            "approval_events": sorted(approvals),
            "mode": summary["mode"],
            "preserve_osg_settings": preserve_osg_settings,
        },
    )
    summary["project_init_summary"] = str(init_summary_path)
    summary["project_init_config"] = str(init_config_path)
    _log(f"initialization summary written to {init_summary_path}", verbose=verbose)
    _log(f"initialization config written to {init_config_path}", verbose=verbose)
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Initialize a Purohit project on the current submit cluster.")
    parser.add_argument("--hosts", required=True, type=Path, help="Host profile YAML file with source/target cluster definitions.")
    parser.add_argument("--source-host", default="cit", help="Source host profile name. Default: cit")
    parser.add_argument("--target-host", default=None, help="Target host profile name. Defaults to hostname auto-detection.")
    parser.add_argument("--source-dir", default=None, help="Source config tree on the source host.")
    parser.add_argument("--working-dir", default=None, help="Deprecated PERerun alias for --source-dir.")
    parser.add_argument("--project-dir", type=Path, default=None, help="Target project dir. Defaults to target host project_dir.")
    parser.add_argument("--apx", default="NRSur7dq4", help="Approximant/config token used to select source INIs.")
    parser.add_argument("--approvals-yaml", type=Path, default=None, help="YAML mapping EVENT: approval-token used to select configs.")
    parser.add_argument("--approval", action="append", default=[], help="Inline approval EVENT=TOKEN. Repeatable; overrides YAML entries.")
    parser.add_argument("--event", action="append", default=[], help="Event to initialize. Repeatable. Omit for all matching events.")
    parser.add_argument("--mode", choices=["auto", "local", "remote"], default="auto", help="auto: local on source host, remote import otherwise.")
    parser.add_argument("--accounting", default="ligo.dev.o4.cbc.pe.bilby")
    parser.add_argument("--accounting-user", default="auto")
    parser.add_argument("--label-suffix", default="_p2")
    parser.add_argument("--overwrite-configs", action="store_true")
    parser.add_argument("--no-reconfigure-existing-configs", action="store_true")
    parser.add_argument("--preserve-osg-settings", action="store_true", help="Keep source OSG/container settings instead of localizing submit INIs.")
    parser.add_argument("--no-cache-config-discovery", action="store_true")
    parser.add_argument("--refresh-config-cache", action="store_true")
    parser.add_argument("--config-cache-file", type=Path, default=None)
    parser.add_argument("--quiet", action="store_true", help="Suppress init-project and PERerun progress output.")
    parser.add_argument("--no-progress", action="store_true", help="Disable progress-style output where supported.")
    parser.add_argument("--data-subdir", default="data")
    parser.add_argument("--submit-suffix", default=".target.ini")
    parser.add_argument("--preserve-root", action="append", default=[])
    parser.add_argument("--rsync-arg", action="append", default=[])
    parser.add_argument("--no-create-token", action="store_true")
    parser.add_argument("--token-file", type=Path, default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    approvals = {str(key): str(value) for key, value in _load_yaml_mapping(args.approvals_yaml).items()}
    approvals.update(_parse_key_value(args.approval, option="--approval"))

    summary = init_project(
        hosts_file=args.hosts,
        source_host_name=args.source_host,
        target_host_name=args.target_host,
        source_dir=args.source_dir,
        working_dir=args.working_dir,
        project_dir=args.project_dir,
        apx=args.apx,
        approvals=approvals,
        events=args.event or None,
        mode=args.mode,
        accounting=args.accounting,
        accounting_user=args.accounting_user,
        label_suffix=args.label_suffix,
        overwrite_configs=args.overwrite_configs,
        reconfigure_existing_configs=not args.no_reconfigure_existing_configs,
        preserve_osg_settings=args.preserve_osg_settings,
        cache_config_discovery=not args.no_cache_config_discovery,
        refresh_config_cache=args.refresh_config_cache,
        config_cache_file=args.config_cache_file,
        verbose=not args.quiet,
        progress=not args.no_progress,
        data_subdir=args.data_subdir,
        submit_suffix=args.submit_suffix,
        preserve_roots=args.preserve_root or None,
        rsync_args=args.rsync_arg or None,
        create_token=not args.no_create_token,
        token_file=args.token_file,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
