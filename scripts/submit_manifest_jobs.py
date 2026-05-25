from __future__ import annotations

import argparse
from pathlib import Path

from reanalyze.manifest_workflow import ManifestRerun


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare and optionally submit manifest-driven Purohit jobs."
    )
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument("--command-template", default="pyRing --config-file {config}")
    parser.add_argument("--event-column", default="event_id")
    parser.add_argument("--config-column", default="config")
    parser.add_argument("--output-column", default="output")
    parser.add_argument("--command-column", default="command")
    parser.add_argument("--application", default="pyring")
    parser.add_argument("--workflow-type", default="manifest")
    parser.add_argument("--request-cpus", type=int, default=1)
    parser.add_argument("--request-memory", default="4GB")
    parser.add_argument("--request-disk", default="4GB")
    parser.add_argument("--max-runtime", type=int, default=None)
    parser.add_argument("--accounting", default=None)
    parser.add_argument("--accounting-user", default="auto")
    parser.add_argument("--env-setup", type=Path, default=None, help="Shell script sourced inside the Condor wrapper via PUROHIT_ENV_SETUP.")
    parser.add_argument("--environment", default=None, help="Extra HTCondor environment string, e.g. 'A=B C=D'.")
    parser.add_argument("--disable-input-staging", action="store_true")
    parser.add_argument("--overwrite-configs", action="store_true")
    parser.add_argument("--no-copy-configs", action="store_true")
    parser.add_argument("--no-write-submit-files", action="store_true")
    parser.add_argument("--submit", action="store_true", help="Submit jobs immediately after preparation.")
    parser.add_argument("--n-jobs", type=int, default=0, help="Number of jobs to submit. 0 means all prepared jobs when --submit is used.")
    return parser.parse_args()


def render_environment(args: argparse.Namespace) -> str | None:
    entries: list[str] = []
    if args.environment:
        entries.append(args.environment)
    if args.env_setup is not None:
        entries.append(f"PUROHIT_ENV_SETUP={args.env_setup.expanduser().resolve()}")
    return " ".join(entries) if entries else None


def print_submit_result(result) -> None:
    if result is None:
        print("[manifest-submit] no submission performed; jobs may already be listed as submitted.")
        return
    items = result if isinstance(result, list) else [result]
    print(f"[manifest-submit] submit_jobs returned {len(items)} result(s)")
    for index, item in enumerate(items):
        if item is None:
            print(f"[manifest-submit] submit result {index}: skipped")
            continue
        stdout = getattr(item, "stdout", None)
        stderr = getattr(item, "stderr", None)
        if stdout:
            print(stdout.rstrip())
        if stderr:
            print(stderr.rstrip())
        if stdout is None and stderr is None:
            print(item)


def main() -> None:
    args = parse_args()
    rerun = ManifestRerun(
        manifest_path=args.manifest,
        project_dir=args.project_dir,
        command_template=args.command_template,
        config_column=args.config_column,
        output_column=args.output_column,
        event_column=args.event_column,
        command_column=args.command_column,
        copy_configs=not args.no_copy_configs,
        overwrite_configs=args.overwrite_configs,
        accounting=args.accounting,
        accounting_user=args.accounting_user,
        request_cpus=args.request_cpus,
        request_memory=args.request_memory,
        request_disk=args.request_disk,
        max_runtime=args.max_runtime,
        environment=render_environment(args),
        enable_input_staging=not args.disable_input_staging,
        workflow_type=args.workflow_type,
        application=args.application,
        verbose=True,
        progress=True,
    )
    rerun.run()
    events = sorted(rerun.config_paths)
    print(f"[manifest-submit] prepared {len(events)} event(s)")
    for event in events:
        print(f"  {event}")

    if not args.no_write_submit_files:
        for event in events:
            submit_file = rerun.write_submit_file(event)
            print(f"[manifest-submit] wrote {submit_file}")

    if args.submit:
        n_jobs = args.n_jobs or len(events)
        print(f"[manifest-submit] submitting {n_jobs} job(s)")
        print_submit_result(rerun.submit_jobs(n_jobs))


if __name__ == "__main__":
    main()
