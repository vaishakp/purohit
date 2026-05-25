"""Manifest-driven HTCondor workflow support.

This module generalizes the Purohit ``PERerun`` monitoring/submission layout to
non-``bilby_pipe`` jobs.  It is intended for workflows that already have a job
manifest, for example the CE-STM pyRing forecast manifest written by
``studies/ce_stm/make_pyring_jobs.py`` in the pyRing fork.

The class keeps the same project layout used by the static monitor:

``project_dir/working/<event>/status.yaml``
    Per-event status ledger.

``project_dir/submitted_jobs.txt``
    One event id per submitted job.

That makes manifest jobs visible to the existing Purohit static monitor and the
per-event resource pages.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pandas as pd

try:
    from reanalyze.input_staging import stage_bilby_inputs
except ImportError:  # pragma: no cover - older purohit checkout
    stage_bilby_inputs = None

from reanalyze.reanalyze import PERerun


DEFAULT_WRAPPER = """#!/usr/bin/env bash
set -euo pipefail

CONFIG_FILE="$1"
OUTPUT_DIR="$2"
COMMAND_TEMPLATE="$3"
EVENT_ID="$4"

mkdir -p "$OUTPUT_DIR"

heartbeat() {
python - "$OUTPUT_DIR" "$EVENT_ID" <<'PY'
import json, os, platform, sys, time
outdir, event = sys.argv[1], sys.argv[2]
payload = {
    "event": event,
    "generated_at": time.time(),
    "hostname": platform.node(),
    "pid": os.getpid(),
}
try:
    import psutil
    payload.update({
        "load_avg_1m": os.getloadavg()[0] if hasattr(os, "getloadavg") else None,
        "memory_available_gb": round(psutil.virtual_memory().available / 1024**3, 3),
    })
except Exception:
    pass
with open(os.path.join(os.path.dirname(outdir), "heartbeat.json"), "w") as f:
    json.dump(payload, f, indent=2, sort_keys=True)
PY
}

heartbeat || true

export OMP_NUM_THREADS="${OMP_NUM_THREADS:-1}"
export MKL_NUM_THREADS="${MKL_NUM_THREADS:-1}"
export OPENBLAS_NUM_THREADS="${OPENBLAS_NUM_THREADS:-1}"
export NUMEXPR_NUM_THREADS="${NUMEXPR_NUM_THREADS:-1}"

if [[ -n "${PUROHIT_ENV_SETUP:-}" ]]; then
    # shellcheck disable=SC1090
    source "${PUROHIT_ENV_SETUP}"
fi

COMMAND="${COMMAND_TEMPLATE//\{config\}/$CONFIG_FILE}"
COMMAND="${COMMAND//\{output\}/$OUTPUT_DIR}"
COMMAND="${COMMAND//\{event\}/$EVENT_ID}"

echo "[purohit] argc=$#"
echo "[purohit] event=${EVENT_ID}"
echo "[purohit] config=${CONFIG_FILE}"
echo "[purohit] output=${OUTPUT_DIR}"
echo "[purohit] command_template=${COMMAND_TEMPLATE}"
echo "[purohit] command=${COMMAND}"

eval "$COMMAND"

heartbeat || true
"""


def _condor_new_syntax_arg(value) -> str:
    """Render one argv element inside HTCondor's new ``arguments`` syntax.

    New syntax requires the whole arguments string to be double-quoted.  Spaces
    delimit argv entries, so a single argv element containing spaces must be
    surrounded by single quotes inside that outer double-quoted string.  Literal
    single quotes inside such an element are doubled; literal double quotes are
    doubled because the outer string is double-quoted.
    """

    text = str(value).replace('"', '""')
    if text == "" or any(ch.isspace() for ch in text):
        return "'" + text.replace("'", "''") + "'"
    return text


def _condor_arguments_line(arguments) -> str:
    rendered = " ".join(_condor_new_syntax_arg(item) for item in arguments)
    return f'arguments = "{rendered}"'


class ManifestRerun(PERerun):
    """Prepare, submit, and monitor jobs from a manifest CSV.

    The expected manifest columns are at least ``event_id``, ``config`` and
    ``output``.  A ``command`` column is optional; if omitted, the
    ``command_template`` argument is used.  The command template may contain
    ``{config}``, ``{output}``, and ``{event}`` placeholders.
    """

    def __init__(
        self,
        manifest_path,
        project_dir,
        command_template="pyRing --config-file {config}",
        config_column="config",
        output_column="output",
        event_column="event_id",
        command_column="command",
        copy_configs=True,
        overwrite_configs=False,
        accounting=None,
        accounting_user="auto",
        request_cpus=1,
        request_memory="4GB",
        request_disk="4GB",
        getenv=True,
        requirements=None,
        max_runtime=None,
        extra_submit_lines=None,
        environment=None,
        enable_input_staging=True,
        verbose=True,
        progress=True,
    ):
        self.manifest_path = Path(manifest_path).expanduser()
        self.command_template = command_template
        self.config_column = config_column
        self.output_column = output_column
        self.event_column = event_column
        self.command_column = command_column
        self.copy_configs = copy_configs
        self.request_cpus = request_cpus
        self.request_memory = request_memory
        self.request_disk = request_disk
        self.getenv = getenv
        self.requirements = requirements
        self.max_runtime = max_runtime
        self.extra_submit_lines = extra_submit_lines or []
        self.environment = environment
        self.enable_input_staging = enable_input_staging
        self.wrapper_path = None
        self.manifest_df = None
        self.manifest_rows = {}

        super().__init__(
            source_dir=self.manifest_path.parent,
            project_dir=project_dir,
            apx="manifest",
            approvals=None,
            overwrite_configs=overwrite_configs,
            reconfigure_existing_configs=False,
            accounting=accounting,
            accounting_user=accounting_user,
            label_suffix="",
            cache_config_discovery=False,
            verbose=verbose,
            progress=progress,
        )

    def _read_manifest(self) -> pd.DataFrame:
        if not self.manifest_path.is_file():
            raise FileNotFoundError(f"Manifest not found: {self.manifest_path}")
        df = pd.read_csv(self.manifest_path)
        required = {self.event_column, self.config_column, self.output_column}
        missing = required.difference(df.columns)
        if missing:
            raise ValueError(f"Manifest {self.manifest_path} is missing columns: {sorted(missing)}")
        df[self.event_column] = df[self.event_column].astype(str)
        return df

    @staticmethod
    def _safe_event_id(raw: str) -> str:
        return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in str(raw))

    def _write_wrapper(self) -> Path:
        bin_dir = self.project_dir / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        wrapper = bin_dir / "run_manifest_job.sh"
        wrapper.write_text(DEFAULT_WRAPPER, encoding="utf-8")
        wrapper.chmod(0o755)
        self.wrapper_path = wrapper
        return wrapper

    def prepare_configs(self, source_dir=None, apx=None, working_dir=None):
        """Prepare ``project_dir/working/<event>`` from the manifest."""

        self._log(f"Preparing manifest workflow from {self.manifest_path}")
        df = self._read_manifest()
        self.manifest_df = df
        self._write_wrapper()

        self.source_dict = {}
        self.config_paths = {}
        self.manifest_rows = {}
        working = self.project_dir / "working"
        working.mkdir(parents=True, exist_ok=True)

        rows = list(df.to_dict("records"))
        for row in self._progress(rows, desc="Preparing manifest jobs", total=len(rows)):
            raw_event = str(row[self.event_column])
            event = self._safe_event_id(raw_event)
            event_dir = working / event
            event_dir.mkdir(parents=True, exist_ok=True)

            source_config = Path(str(row[self.config_column])).expanduser()
            if not source_config.is_absolute():
                for base in (Path.cwd(), self.manifest_path.parent):
                    candidate = base / source_config
                    if candidate.exists():
                        source_config = candidate
                        break
            if not source_config.is_file():
                raise FileNotFoundError(f"Config for event {event} not found: {row[self.config_column]}")

            if self.copy_configs:
                dest_config = event_dir / source_config.name
                if not dest_config.exists() or self.overwrite_configs:
                    shutil.copy2(source_config, dest_config)
                config_path = dest_config
            else:
                config_path = source_config

            output = Path(str(row[self.output_column])).expanduser()
            if not output.is_absolute():
                output = event_dir / output.name

            row = dict(row)
            row["_event"] = event
            row["_config_path"] = str(config_path)
            row["_output_path"] = str(output)
            self.source_dict[event] = str(source_config)
            self.config_paths[event] = config_path
            self.manifest_rows[event] = row
            self.update_job_status_file(
                event,
                {
                    "status": "pending",
                    "jobid": None,
                    "config": str(config_path),
                    "output": str(output),
                    "submit_ini": str(config_path),
                },
            )

        self._log(f"Prepared {len(self.config_paths)} manifest job(s)")
        return {}, self.config_paths

    def reconfigure(self):
        self._log("Manifest workflow does not reconfigure configs; skipping")

    def _submit_file_for_event(self, event: str) -> Path:
        return self.event_dir(event) / "job.submit"

    def _command_for_event(self, event: str) -> str:
        row = self.manifest_rows[event]
        value = row.get(self.command_column)
        if isinstance(value, str) and value.strip():
            if "pyRing" in value and "--config-file" in value:
                return self.command_template
            return value
        return self.command_template

    def _maybe_stage_config(self, event: str, config_path: Path) -> tuple[Path, dict]:
        if not self.enable_input_staging or stage_bilby_inputs is None:
            return config_path, {"staging_enabled": False}
        staged = stage_bilby_inputs(self.project_dir, event, config_path)
        info = {
            "staging_enabled": staged.enabled,
            "staged_config": str(staged.config_path) if staged.enabled else None,
            "input_manifest": None if staged.manifest_path is None else str(staged.manifest_path),
            "staged_input_count": len(staged.copied_files),
            "transfer_enabled": staged.transfer_enabled,
            "transfer_target": staged.transfer_target,
        }
        return staged.config_path, info

    def write_submit_file(self, event: str) -> Path:
        if self.wrapper_path is None:
            self._write_wrapper()
        row = self.manifest_rows[event]
        event_dir = self.event_dir(event)
        event_dir.mkdir(parents=True, exist_ok=True)

        config_path, staging_info = self._maybe_stage_config(event, Path(row["_config_path"]))
        row["_submit_config_path"] = str(config_path)
        row["_staging_info"] = staging_info
        output_path = Path(row["_output_path"]).resolve()
        command_template = self._command_for_event(event)

        arguments = [str(Path(config_path).resolve()), str(output_path), command_template, event]
        lines = [
            "# Auto-generated by Purohit ManifestRerun",
            "universe = vanilla",
            f"executable = {self.wrapper_path.resolve()}",
            "initialdir = " + str(event_dir.resolve()),
            _condor_arguments_line(arguments),
            f"request_cpus = {self.request_cpus}",
            f"request_memory = {self.request_memory}",
            f"request_disk = {self.request_disk}",
            f"getenv = {str(self.getenv).lower()}",
            "notification = Never",
            "log = condor.log",
            "output = condor.out",
            "error = condor.err",
        ]
        if self.accounting:
            lines.append(f'+AccountingGroup = "{self.accounting}"')
        if self.accounting_user:
            lines.append(f'+AccountingGroupUser = "{self.accounting_user}"')
        if self.requirements:
            lines.append(f"requirements = {self.requirements}")
        if self.max_runtime is not None:
            lines.append(f"+MaxRuntime = {int(self.max_runtime)}")
        if self.environment:
            lines.append(f'environment = "{self.environment}"')
        lines.extend(self.extra_submit_lines)
        lines.append("queue 1")
        submit_file = self._submit_file_for_event(event)
        submit_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return submit_file

    def _parse_jobid_from_condor_submit_stdout(self, stdout: str) -> str:
        patterns = [
            r"submitted\s+to\s+cluster\s+(\d+)",
            r"cluster\s+(\d+)(?:\.\d+)?",
            r"\b(\d+)\s+job\(s\)\s+submitted",
        ]
        for pattern in patterns:
            match = re.search(pattern, stdout, re.IGNORECASE)
            if match:
                return match.group(1)
        matches = re.findall(r"\b(\d+)(?:\.\d+)?\b", stdout)
        if matches:
            return matches[-1]
        raise RuntimeError(f"Could not parse Condor cluster id from condor_submit output:\n{stdout}")

    def submit_one_job(self, event):
        self.parse_submitted_jobs_list()
        if event not in self.config_paths:
            raise KeyError(f"Unknown event {event!r}. Known events: {sorted(self.config_paths)}")
        if event in self.submitted_jobs:
            self._log(f"Event {event}: already submitted; skipping", level="WARNING")
            return None
        submit_file = self.write_submit_file(event)
        command = ["condor_submit", str(submit_file)]
        self._log(f"Event {event}: submitting {submit_file}")
        out = self.run_cmd(command, shell=False)
        jobid = self._parse_jobid_from_condor_submit_stdout(out.stdout)
        self.add_to_submitted_jobs_list(event)
        submit_config = self.manifest_rows[event].get("_submit_config_path", self.manifest_rows[event].get("_config_path"))
        updates = {
            "jobid": jobid,
            "status": "submitted",
            "submit_file": str(submit_file),
            "submitted_config": str(submit_config),
            "submit_ini": str(submit_config),
        }
        updates.update(self.manifest_rows[event].get("_staging_info", {}))
        self.update_job_status_file(event, updates)
        self._log(f"Event {event}: submitted successfully with Condor cluster id {jobid}")
        return out

    def check_for_completion(self, event):
        row = self.manifest_rows.get(event, {})
        output = Path(row.get("_output_path", self.event_dir(event) / "output"))
        if (output / "Nested_sampler" / "posterior.dat").is_file():
            return "completed"
        if (output / "_SUCCESS").is_file():
            return "completed"
        return "incomplete"

    def load(self):
        self.prepare_configs()
        return self.source_dict

    def run(self):
        self._log("Starting manifest workflow")
        self.prepare_configs()
        self.parse_submitted_jobs_list()
        status = self.all_job_status()
        self._log("Manifest workflow ready")
        return status
