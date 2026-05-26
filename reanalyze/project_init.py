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


def init_project(
    *,
    hosts_file: Path,
    source_host_name: str,
    source_dir: str,
    project_dir: Path | None,
    apx: str,
    target_host_name: str | None = None,
    events: list[str] | None = None,
    mode: str = "auto",
    accounting: str | None = "ligo.dev.o4.cbc.pe.bilby",
    accounting_user: str = "auto",
    label_suffix: str = "_p2",
    overwrite_configs: bool = False,
    reconfigure_existing_configs: bool = True,
    data_subdir: str = "data",
    submit_suffix: str = ".target.ini",
    preserve_roots: list[str] | None = None,
    rsync_args: list[str] | None = None,
    create_token: bool = True,
    token_file: Path | None = None,
) -> dict[str, Any]:
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

    summary: dict[str, Any] = {
        "generated_at": time.time(),
        "mode": "local" if use_local else "remote",
        "current_host": _profile_name(current),
        "source_host": source.name,
        "target_host": target.name,
        "source_dir": source_dir,
        "target_project_dir": str(target_project),
        "apx": apx,
        "events_requested": events or [],
    }

    if use_local:
        rerun = PERerun(
            source_dir=source_dir,
            project_dir=target_project,
            apx=apx,
            accounting=accounting,
            accounting_user=accounting_user,
            label_suffix=label_suffix,
            overwrite_configs=overwrite_configs,
            reconfigure_existing_configs=reconfigure_existing_configs,
        )
        rerun.prepare_configs()
        if events:
            selected = set(events)
            rerun.config_paths = {event: path for event, path in rerun.config_paths.items() if event in selected}
            rerun.source_dict = {event: path for event, path in rerun.source_dict.items() if event in selected}
        rerun.reconfigure()
        rerun.parse_submitted_jobs_list()
        summary["events"] = [
            {"event": event, "submit_ini": str(path), "dependency_count": None}
            for event, path in sorted(rerun.config_paths.items())
        ]
    elif use_remote:
        remote_summary = import_events(
            hosts_file=hosts_file,
            source_host_name=source.name,
            target_host_name=target.name,
            source_dir=source_dir,
            target_project_dir=target_project,
            apx=apx,
            events=events or None,
            data_subdir=data_subdir,
            submit_suffix=submit_suffix,
            preserve_roots=preserve_roots,
            rsync_args=rsync_args,
        )
        summary["events"] = remote_summary.get("events", [])
        summary["remote_import"] = remote_summary
    else:  # pragma: no cover - guarded by mode logic above
        raise RuntimeError("unreachable project initialization mode")

    if create_token:
        summary["token_file"] = str(_ensure_token(target_project, token_file))

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
            "mode": summary["mode"],
        },
    )
    summary["project_init_summary"] = str(init_summary_path)
    summary["project_init_config"] = str(init_config_path)
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Initialize a Purohit project on the current submit cluster.")
    parser.add_argument("--hosts", required=True, type=Path, help="Host profile YAML file with source/target cluster definitions.")
    parser.add_argument("--source-host", default="cit", help="Source host profile name. Default: cit")
    parser.add_argument("--target-host", default=None, help="Target host profile name. Defaults to hostname auto-detection.")
    parser.add_argument("--source-dir", required=True, help="Source config tree on the source host.")
    parser.add_argument("--project-dir", type=Path, default=None, help="Target project dir. Defaults to target host project_dir.")
    parser.add_argument("--apx", required=True, help="Approximant/config token used to select source INIs.")
    parser.add_argument("--event", action="append", default=[], help="Event to initialize. Repeatable. Omit for all matching events.")
    parser.add_argument("--mode", choices=["auto", "local", "remote"], default="auto", help="auto: local on source host, remote import otherwise.")
    parser.add_argument("--accounting", default="ligo.dev.o4.cbc.pe.bilby")
    parser.add_argument("--accounting-user", default="auto")
    parser.add_argument("--label-suffix", default="_p2")
    parser.add_argument("--overwrite-configs", action="store_true")
    parser.add_argument("--no-reconfigure-existing-configs", action="store_true")
    parser.add_argument("--data-subdir", default="data")
    parser.add_argument("--submit-suffix", default=".target.ini")
    parser.add_argument("--preserve-root", action="append", default=[])
    parser.add_argument("--rsync-arg", action="append", default=[])
    parser.add_argument("--no-create-token", action="store_true")
    parser.add_argument("--token-file", type=Path, default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = init_project(
        hosts_file=args.hosts,
        source_host_name=args.source_host,
        target_host_name=args.target_host,
        source_dir=args.source_dir,
        project_dir=args.project_dir,
        apx=args.apx,
        events=args.event or None,
        mode=args.mode,
        accounting=args.accounting,
        accounting_user=args.accounting_user,
        label_suffix=args.label_suffix,
        overwrite_configs=args.overwrite_configs,
        reconfigure_existing_configs=not args.no_reconfigure_existing_configs,
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
