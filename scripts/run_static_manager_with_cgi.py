from __future__ import annotations

import argparse
from pathlib import Path
import time

from reanalyze.static_manager import atomic_write_json, process_command_file
from reanalyze.static_monitor import publish_once


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Purohit static manager with a CGI command URL rendered into the page.")
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument("--webdir", required=True, type=Path)
    parser.add_argument("--command-url", required=True, help="CGI URL used by browser buttons, e.g. /~user/cgi-bin/purohit_command.cgi")
    parser.add_argument("--command-file", type=Path, default=None, help="JSON command file. Defaults to project_dir/control/commands.json.")
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument("--plot-interval", type=int, default=300)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--no-history", action="store_true")
    parser.add_argument("--heartbeat-filename", default="heartbeat.json")
    parser.add_argument("--max-artifacts-per-event", type=int, default=40)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    project_dir = args.project_dir.expanduser().resolve()
    webdir = args.webdir.expanduser().resolve()
    command_file = (args.command_file or project_dir / "control" / "commands.json").expanduser().resolve()
    command_file.parent.mkdir(parents=True, exist_ok=True)
    if not command_file.exists():
        atomic_write_json(command_file, {"commands": []})

    last_plot_publish = 0.0
    while True:
        results = process_command_file(project_dir, command_file)
        now = time.time()
        copy_outputs = now - last_plot_publish >= args.plot_interval
        publish_once(
            project_dir,
            webdir,
            include_history=not args.no_history,
            heartbeat_filename=args.heartbeat_filename,
            copy_outputs=copy_outputs,
            command_file=command_file,
            command_url=args.command_url,
            max_artifacts_per_event=args.max_artifacts_per_event,
        )
        if copy_outputs:
            last_plot_publish = now
        print(f"Processed {len(results)} command(s); published manager page to {webdir} at {time.strftime('%Y-%m-%d %H:%M:%S')}")
        if args.once:
            return
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
