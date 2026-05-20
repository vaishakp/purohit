"""Generate or submit an HTCondor job for the static webdir monitor.

The recommended pattern is a short one-shot Condor job that runs
``publish_web_monitor.py --once`` and exits. A cron job, user timer, DAGMan node,
or manual invocation can submit this one-shot refresh job periodically.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import shlex
import subprocess
import sys
from typing import Iterable


DEFAULT_SUBMIT_NAME = "static_monitor.sub"


def quote_arg(value: str | Path) -> str:
    """Return a shell-safe representation for a Condor argument string."""

    return shlex.quote(str(value))


def condor_line(key: str, value: object | None) -> str | None:
    """Format one submit-description line, skipping ``None`` values."""

    if value is None:
        return None
    return f"{key} = {value}"


def build_arguments(args: argparse.Namespace) -> str:
    """Build the command-line arguments for ``publish_web_monitor.py``."""

    monitor_script = args.repo_dir / "scripts" / "publish_web_monitor.py"
    parts: list[str] = [
        quote_arg(monitor_script),
        "--project-dir",
        quote_arg(args.project_dir),
        "--webdir",
        quote_arg(args.webdir),
        "--heartbeat-filename",
        quote_arg(args.heartbeat_filename),
    ]

    if args.no_history:
        parts.append("--no-history")

    if args.mode == "once":
        parts.append("--once")
    else:
        parts.extend(["--interval", str(args.interval)])

    return " ".join(parts)


def build_submit_description(args: argparse.Namespace) -> str:
    """Build an HTCondor submit description for the monitor publisher."""

    log_dir = args.log_dir.expanduser().resolve()
    repo_dir = args.repo_dir.expanduser().resolve()

    lines: list[str | None] = [
        condor_line("universe", "vanilla"),
        condor_line("executable", quote_arg(args.python_executable)),
        condor_line("arguments", build_arguments(args)),
        condor_line("initialdir", quote_arg(repo_dir)),
        condor_line("request_cpus", args.request_cpus),
        condor_line("request_memory", args.request_memory),
        condor_line("request_disk", args.request_disk),
        condor_line("output", quote_arg(log_dir / "static-monitor.$(Cluster).$(Process).out")),
        condor_line("error", quote_arg(log_dir / "static-monitor.$(Cluster).$(Process).err")),
        condor_line("log", quote_arg(log_dir / "static-monitor.$(Cluster).log")),
        condor_line("accounting_group", args.accounting_group),
        condor_line("accounting_group_user", args.accounting_group_user),
        condor_line("requirements", args.requirements),
        condor_line("+JobFlavour", quote_arg(args.job_flavour) if args.job_flavour else None),
        condor_line("environment", quote_arg(args.environment) if args.environment else None),
        condor_line("getenv", "True" if args.getenv else None),
        "queue 1",
    ]

    return "\n".join(line for line in lines if line is not None) + "\n"


def write_submit_file(path: Path, content: str) -> None:
    """Write a Condor submit file, creating parent directories if needed."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def run_condor_submit(submit_file: Path, dry_run: bool = False) -> subprocess.CompletedProcess[str] | None:
    """Run ``condor_submit`` unless ``dry_run`` is true."""

    command = ["condor_submit", str(submit_file)]
    print(" ".join(quote_arg(part) for part in command))
    if dry_run:
        return None
    return subprocess.run(command, check=True, capture_output=True, text=True)


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate or submit an HTCondor job for the static Purohit monitor."
    )
    parser.add_argument("--project-dir", required=True, type=Path, help="Purohit project directory containing submitted_jobs.txt and working/.")
    parser.add_argument("--webdir", required=True, type=Path, help="Static monitor output directory served by webdir/PESummary infrastructure.")
    parser.add_argument("--repo-dir", default=Path.cwd(), type=Path, help="Repository root containing scripts/publish_web_monitor.py.")
    parser.add_argument("--python-executable", default=sys.executable, help="Python executable to run in the Condor job.")
    parser.add_argument("--submit-file", type=Path, default=None, help="Path for the generated submit file. Defaults to <project-dir>/monitor-condor/static_monitor.sub.")
    parser.add_argument("--log-dir", type=Path, default=None, help="Directory for Condor output/error/log files. Defaults to <project-dir>/monitor-condor/logs.")
    parser.add_argument("--mode", choices=["once", "loop"], default="once", help="Submit a one-shot refresh job or a long-running loop job.")
    parser.add_argument("--interval", type=int, default=300, help="Refresh interval for loop mode.")
    parser.add_argument("--no-history", action="store_true", help="Pass --no-history to publish_web_monitor.py.")
    parser.add_argument("--heartbeat-filename", default="heartbeat.json", help="Per-event heartbeat filename relative to project_dir/working/<event>/.")
    parser.add_argument("--request-cpus", default="1", help="Condor request_cpus value.")
    parser.add_argument("--request-memory", default="512MB", help="Condor request_memory value.")
    parser.add_argument("--request-disk", default="100MB", help="Condor request_disk value.")
    parser.add_argument("--accounting-group", default=None, help="Optional Condor accounting_group.")
    parser.add_argument("--accounting-group-user", default=None, help="Optional Condor accounting_group_user.")
    parser.add_argument("--requirements", default=None, help="Optional raw Condor requirements expression.")
    parser.add_argument("--job-flavour", default=None, help="Optional site-specific +JobFlavour value.")
    parser.add_argument("--environment", default=None, help="Optional Condor environment string.")
    parser.add_argument("--getenv", action="store_true", help="Set getenv = True in the submit file.")
    parser.add_argument("--submit", action="store_true", help="Run condor_submit after writing the submit file.")
    parser.add_argument("--dry-run", action="store_true", help="Print the condor_submit command without running it.")

    args = parser.parse_args(argv)
    args.project_dir = args.project_dir.expanduser().resolve()
    args.webdir = args.webdir.expanduser().resolve()
    args.repo_dir = args.repo_dir.expanduser().resolve()

    if args.log_dir is None:
        args.log_dir = args.project_dir / "monitor-condor" / "logs"
    else:
        args.log_dir = args.log_dir.expanduser().resolve()

    if args.submit_file is None:
        args.submit_file = args.project_dir / "monitor-condor" / DEFAULT_SUBMIT_NAME
    else:
        args.submit_file = args.submit_file.expanduser().resolve()

    return args


def main(argv: Iterable[str] | None = None) -> None:
    args = parse_args(argv)
    args.log_dir.mkdir(parents=True, exist_ok=True)
    submit_description = build_submit_description(args)
    write_submit_file(args.submit_file, submit_description)
    print(f"Wrote {args.submit_file}")

    if args.submit:
        result = run_condor_submit(args.submit_file, dry_run=args.dry_run)
        if result is not None:
            if result.stdout:
                print(result.stdout)
            if result.stderr:
                print(result.stderr, file=sys.stderr)


if __name__ == "__main__":
    main()
