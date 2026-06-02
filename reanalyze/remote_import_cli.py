"""Command-line interface for explicit remote event import."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from reanalyze.remote_import import import_events


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import selected bilby event configs from a source host to a target project.")
    parser.add_argument("--hosts", required=True, type=Path, help="Host profile YAML file.")
    parser.add_argument("--source-host", required=True, help="Source host profile name, e.g. cit.")
    parser.add_argument("--target-host", required=True, help="Target/submit host profile name, e.g. gwave.")
    parser.add_argument("--source-dir", required=True, help="Large source directory on the source host.")
    parser.add_argument("--target-project-dir", type=Path, default=None, help="Target project dir. Defaults to target host project_dir from hosts YAML.")
    parser.add_argument("--apx", required=True, help="Approximant/config token used to select source INIs.")
    parser.add_argument("--event", action="append", default=[], help="Event to import. Repeatable. If omitted, all matching events are imported.")
    parser.add_argument("--data-subdir", default="data", help="Per-event data subdirectory name.")
    parser.add_argument("--submit-suffix", default=".target.ini", help="Suffix for target submit INI.")
    parser.add_argument("--preserve-root", action="append", default=[], help="Absolute roots to preserve and not copy/rewrite. Repeatable.")
    parser.add_argument("--rsync-arg", action="append", default=[], help="Extra/override rsync args. If omitted, uses safe defaults.")
    parser.add_argument("--preserve-osg-settings", action="store_true", help="Keep source OSG/container settings instead of localizing the target INI.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = import_events(
        hosts_file=args.hosts,
        source_host_name=args.source_host,
        target_host_name=args.target_host,
        source_dir=args.source_dir,
        target_project_dir=args.target_project_dir,
        apx=args.apx,
        events=args.event or None,
        data_subdir=args.data_subdir,
        submit_suffix=args.submit_suffix,
        preserve_roots=args.preserve_root or None,
        rsync_args=args.rsync_arg or None,
        preserve_osg_settings=args.preserve_osg_settings,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
