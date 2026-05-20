"""Utilities for preparing and resubmitting bilby_pipe parameter-estimation jobs.

The :class:`PERerun` workflow copies pre-generated bilby_pipe INI files into a
project-local working area, edits selected configuration entries, submits jobs to
HTCondor through ``bilby_pipe --submit``, and keeps a small ledger of submitted
jobs and their most recent status.
"""

import os
import re
import subprocess
from pathlib import Path
import shutil
import getpass

import numpy as np
import pandas as pd
import yaml
from tqdm import tqdm

from reanalyze.utils import get_condor_job_status
from waveformtools.waveformtools import message


class PERerun:
    """Prepare, reconfigure, submit, and monitor bilby_pipe reruns.

    Parameters
    ----------
    working_dir : str or pathlib.Path
        Directory containing the original bilby_pipe working tree. Each event is
        expected to live under a first-level subdirectory of this path.
    project_dir : str or pathlib.Path
        Local project directory where copied INI files, job ledgers, and status
        files are stored.
    apx : str, optional
        Waveform/approximant token used to discover matching INI files. Matching
        is case-insensitive and is applied to the INI file name.
    approvals : dict[str, str] or None, optional
        Optional mapping from event name to a substring that selects the approved
        config file when multiple matching INI files exist for that event. If an
        approval token matches no files for an event, the selector warns and
        falls back to the last sorted available config for that event.
    overwrite_configs : bool, optional
        If true, overwrite existing copied INI files during config preparation.
    verbose : bool, optional
        If true, print high-level progress and status messages to stdout.
    progress : bool, optional
        If true, show tqdm progress bars for multi-event operations.

    Notes
    -----
    The class changes the current working directory to ``project_dir`` during
    initialization because bilby_pipe submission can be sensitive to the current
    directory relative to the configured output directory.
    """

    def __init__(self,
                 working_dir,
                 project_dir,
                 apx='NRSur7dq4',
                 approvals=None,
                 overwrite_configs=False,
                 verbose=True,
                 progress=True):

        self.working_dir = Path(working_dir)
        self.project_dir = Path(project_dir)
        self.apx = apx
        self.approvals = approvals or {}
        self.overwrite_configs = overwrite_configs
        self.verbose = verbose
        self.progress = progress

        self.project_dir.mkdir(parents=True, exist_ok=True)

        os.chdir(self.project_dir)

        self.submitted_jobs_list_file = self.project_dir / "submitted_jobs.txt"
        self.resume = self.submitted_jobs_list_file.is_file()

        if not self.resume:
            self.submitted_jobs_list_file.touch()
            self._log(f"Initialized new project at {self.project_dir}")
        else:
            self._log(f"Resuming existing project at {self.project_dir}")

        self._log(f"Source working directory: {self.working_dir}", level="DEBUG")
        self._log(f"Approximant/config token: {self.apx}", level="DEBUG")

    def _log(self, message_text, level="INFO"):
        """Print a consistently formatted status message when verbose output is enabled."""

        if self.verbose:
            print(f"[purohit] {level}: {message_text}")

    def _progress(self, iterable, *, desc, total=None, unit="it"):
        """Wrap an iterable in tqdm when progress bars are enabled."""

        return tqdm(iterable, desc=desc, total=total, unit=unit, disable=not self.progress)

    def event_dir(self, event):
        """Return the project-local working directory for an event."""
        return Path(os.path.dirname(self.config_paths[event]))

    def run_cmd(self,
            command,
            shell=True,
            capture_output=True,
            check=True,
            text=True):
        """Run a shell or subprocess command and re-raise failures verbosely.

        Parameters mirror :func:`subprocess.run`. On failure, stdout/stderr are
        printed before the original ``CalledProcessError`` is re-raised.
        """

        try:
            out = subprocess.run(command, shell=shell, capture_output=capture_output, check=check, text=text)
        except subprocess.CalledProcessError as e:
            self._log(f"Command failed with exit code {e.returncode}: {command}", level="ERROR")
            if e.stdout:
                self._log(f"stdout:\n{e.stdout}", level="ERROR")
            if e.stderr:
                self._log(f"stderr:\n{e.stderr}", level="ERROR")
            raise

        return out

    def _scan_matching_ini_files(self):
        """Scan ``working_dir`` for matching INI files with live config/event counters."""

        apx_lower = self.apx.lower()
        files = []
        event_names = set()
        scanned = 0
        matched = 0
        last_reported_match_count = 0
        last_reported_event_count = 0

        scan_iter = self.working_dir.rglob("*.ini")
        if self.progress:
            scan_iter = tqdm(scan_iter, desc="Scanning INI files", unit="file")

        for path in scan_iter:
            scanned += 1
            if apx_lower in path.name.lower():
                matched += 1
                files.append(path)

                rel_path = path.relative_to(self.working_dir)
                if rel_path.parts:
                    event_names.add(rel_path.parts[0])

            if self.progress:
                scan_iter.set_postfix(
                    scanned=scanned,
                    configs=matched,
                    events=len(event_names),
                    refresh=True,
                )
            elif self.verbose and (
                matched >= last_reported_match_count + 25
                or len(event_names) >= last_reported_event_count + 10
            ):
                last_reported_match_count = matched
                last_reported_event_count = len(event_names)
                self._log(
                    f"Config scan progress: {matched} matching config(s), "
                    f"{len(event_names)} event(s), {scanned} INI file(s) scanned"
                )

        files = sorted(files)
        self._log(
            f"Config scan complete: scanned {scanned} INI file(s), "
            f"found {matched} matching config(s), discovered {len(event_names)} event(s)"
        )
        return files

    def find_bilby_configs(self):
        """Discover one source bilby_pipe INI file per event.

        Returns
        -------
        dict[str, str]
            Mapping from event name to selected source INI path.

        Raises
        ------
        FileNotFoundError
            If ``working_dir`` is missing or no matching INI files are found.
        """

        self._log(f"Searching for bilby_pipe INI files matching '*{self.apx}*.ini' under {self.working_dir}")

        if not self.working_dir.is_dir():
            raise FileNotFoundError(f"working_dir does not exist or is not a directory: {self.working_dir}")

        files = self._scan_matching_ini_files()

        if not files:
            raise FileNotFoundError(
                f"No bilby_pipe ini files matching '*{self.apx}*.ini' found under {self.working_dir}"
            )

        self._log(f"Found {len(files)} matching INI file(s)")

        event_sdict = {}
        for item in files:
            rel_path = item.relative_to(self.working_dir)
            if not rel_path.parts:
                continue
            event_name = rel_path.parts[0]
            event_sdict.setdefault(event_name, []).append(str(item))

        self._log(f"Grouped configs into {len(event_sdict)} event(s)")

        event_dict = {}

        for event in self._progress(sorted(event_sdict), desc="Selecting configs", total=len(event_sdict)):
            event_files = sorted(event_sdict[event])

            if event in self.approvals:
                tfile = self.approvals[event]
                fil_files = [item for item in event_files if tfile in item]
                if not fil_files:
                    event_file = event_files[-1]
                    self._log(
                        f"Event {event}: approval token {tfile!r} matched no configs; "
                        f"falling back to last sorted available config {event_file}. "
                        f"Available files: {event_files}",
                        level="WARNING",
                    )
                else:
                    if len(fil_files) > 1:
                        self._log(f"Event {event}: approval token matched {len(fil_files)} files; using first sorted match", level="WARNING")
                    event_file = fil_files[0]
                    self._log(f"Event {event}: selected approved config {event_file}", level="DEBUG")
            else:
                event_file = event_files[0]
                self._log(f"Event {event}: selected config {event_file}", level="DEBUG")

            event_dict.update({event: event_file})

        self._log(f"Selected one source config for each of {len(event_dict)} event(s)")
        return event_dict

    def copy_inis(self):
        """Copy selected source INI files into ``project_dir/working/<event>``.

        Returns
        -------
        tuple[dict[str, str], dict[str, pathlib.Path]]
            The first item records copied destination paths for fresh projects.
            The second maps event names to project-local INI paths.
        """

        all_outs = {}
        config_paths = {}
        project_dir = Path(self.project_dir)
        working_dir = project_dir / "working"
        working_dir.mkdir(parents=True, exist_ok=True)

        copied_count = 0
        skipped_count = 0

        items = list(self.source_dict.items())
        for key, val in self._progress(items, desc="Copying configs", total=len(items)):
            event_dir = working_dir / key
            event_dir.mkdir(parents=True, exist_ok=True)

            src_path = Path(val)
            filename = src_path.name
            dest_path = event_dir / filename

            if self.resume:
                skipped_count += 1
                self._log(f"Event {key}: resume mode; using existing local config path {dest_path}", level="DEBUG")
            elif dest_path.exists() and not self.overwrite_configs:
                skipped_count += 1
                self._log(f"Event {key}: keeping existing copied config {dest_path}")
                all_outs.update({key: "skipped_existing"})
            else:
                copied_path = shutil.copy2(src_path, dest_path)
                copied_count += 1
                self._log(f"Event {key}: copied {src_path} -> {dest_path}", level="DEBUG")
                all_outs.update({key: copied_path})

            config_paths.update({key: dest_path})

        self._log(
            f"Config copy step complete: {copied_count} copied, {skipped_count} skipped, {len(config_paths)} local config path(s) tracked"
        )
        return all_outs, config_paths

    def reconfigure_one_ini(self, event):
        """Edit one copied bilby_pipe INI for resubmission.

        The current implementation updates the label, accounting user, output
        directory, web directory, resource requests, analysis executable,
        submission backend, selected spin priors, and sampler kwargs. For
        ``NRSur7dq4`` runs it also writes the NRSur7dq4 HDF5 transfer path.
        """

        config_path = self.config_paths[event]
        user = getpass.getuser()
        webdir = self.project_dir / "webdir"
        outdir = f"{os.path.dirname(config_path)}/pe"

        self._log(f"Event {event}: reconfiguring {config_path}")
        self._log(f"Event {event}: outdir={outdir}", level="DEBUG")
        self._log(f"Event {event}: webdir={webdir}", level="DEBUG")
        self._log(f"Event {event}: accounting-user={user}", level="DEBUG")

        replacements = [
            (f"/^label/c\\label={event}_p2", "label"),
            (f"/^accounting-user/c\\accounting-user={user}", "accounting-user"),
            (f"/^outdir/c\\outdir={outdir}", "outdir"),
            (f"/^webdir/c\\webdir={webdir}", "webdir"),
            ("/^request-memory=/c\\request-memory=8", "request-memory"),
            ("/^request-disk/c\\request-disk=16", "request-disk"),
        ]

        for sed_expr, field_name in replacements:
            command = f"sed -i '{sed_expr}' {config_path}"
            self.run_cmd(command, shell=True, capture_output=True, text=True)
            self._log(f"Event {event}: updated {field_name}", level="DEBUG")

        bilby_pipe_analysis_path = shutil.which("bilby_pipe_analysis")
        if bilby_pipe_analysis_path is None:
            raise FileNotFoundError(
                "Could not find 'bilby_pipe_analysis' on PATH. Activate the intended bilby_pipe environment first."
            )

        command = f"sed -i '/^analysis-executable=/c\\analysis-executable={bilby_pipe_analysis_path}' {config_path}"
        self.run_cmd(command, shell=True, capture_output=True, text=True)
        self._log(f"Event {event}: analysis-executable={bilby_pipe_analysis_path}", level="DEBUG")

        command = f"sed -i '/^submit=/c\\submit=condor' {config_path}"
        self.run_cmd(command, shell=True, capture_output=True, text=True)
        self._log(f"Event {event}: submit backend set to condor", level="DEBUG")

        if self.apx == 'NRSur7dq4':
            command = f"sed -i '/^additional-transfer-paths=/c\\additional-transfer-paths=[\/scratch\/lalsimulation/NRSur7dq4_v1.0.h5]' {config_path}"
            self.run_cmd(command, shell=True, capture_output=True, text=True)
            self._log(f"Event {event}: configured NRSur7dq4 transfer path", level="DEBUG")

        cmd = [
            "sed", "-Ei",
            r"s/a_1[[:space:]]*=[[:space:]]*Uniform[[:space:]]*\([[:space:]]*name[[:space:]]*=[[:space:]]*'a_1',[[:space:]]*minimum[[:space:]]*=[[:space:]]*0,[[:space:]]*maximum[[:space:]]*=[[:space:]]*0\.99[[:space:]]*\)/a_1 = PowerLaw(name='a_1', minimum=0, maximum=1, alpha=2)/g",
            config_path
            ]

        self.run_cmd(cmd, shell=False)
        self._log(f"Event {event}: updated a_1 prior if matching line was present", level="DEBUG")

        cmd = [
            "sed", "-Ei",
            r"s/[[:space:]]*a_2[[:space:]]*=[[:space:]]*Uniform[[:space:]]*\([[:space:]]*name[[:space:]]*=[[:space:]]*'a_2',[[:space:]]*minimum[[:space:]]*=[[:space:]]*0,[[:space:]]*maximum[[:space:]]*=[[:space:]]*0\.99[[:space:]]*\)/ a_2 = PowerLaw(name='a_2', minimum=0, maximum=1, alpha=2)/g",
            config_path
            ]

        self.run_cmd(cmd, shell=False)
        self._log(f"Event {event}: updated a_2 prior if matching line was present", level="DEBUG")

        sampler_kwargs = "sampler-kwargs={'nlive': 2000, 'naccept': 60, 'check_point_plot': True, 'check_point_delta_t': 1800, 'print_method': 'interval-60', 'sample': 'acceptance-walk', 'npool': 16, 'dlogz': 0.01}"
        command = f"sed -i '/^sampler-kwargs/c\\{sampler_kwargs}' {config_path}"
        self.run_cmd(command)
        self._log(f"Event {event}: updated sampler kwargs")
        self._log(f"Event {event}: reconfiguration complete")

    def prepare_configs(self,
                        working_dir="/home/pe.o4/GWTC4/working",
                        apx='NRSur7dq4'):
        """Discover source configs and copy them into the local project tree.

        The ``working_dir`` and ``apx`` arguments are retained for backward
        compatibility but are not currently used; the object attributes set at
        initialization determine the search path and approximant token.
        """

        self._log("Preparing local config files")
        self.source_dict = self.find_bilby_configs()
        outs, config_paths = self.copy_inis()
        self.config_paths = config_paths
        self._log(f"Prepared {len(self.config_paths)} local config path(s)")
        return outs, config_paths

    def reconfigure(self):
        """Reconfigure copied INI files for a fresh project.

        Existing projects are treated as resume operations and are not
        reconfigured automatically.
        """
        if self.resume:
            self._log("Resume mode detected; skipping automatic INI reconfiguration")
            return

        items = list(self.config_paths.items())
        self._log(f"Reconfiguring {len(items)} copied INI file(s)")
        for event, config_path in self._progress(items, desc="Reconfiguring configs", total=len(items)):
            self.reconfigure_one_ini(event)
        self._log("INI reconfiguration step complete")

    def read_job_status(self, event):
        """Read the persisted status and Condor cluster id for one event."""

        job_file = Path(os.path.dirname(self.config_paths[event])) / "status.yaml"

        if not os.path.isfile(job_file):
            status = 'pending'
            jobid = None

        else:
            with open(job_file, 'r') as file:
                info = yaml.safe_load(file) or {}

            status = info.get('status', 'unknown')
            jobid = info.get('jobid')

        return status, jobid

    def all_job_status(self):
        """Query all configured jobs and return a status DataFrame.

        The most recent queried status is also persisted back to each event's
        ``status.yaml`` file.
        """

        self._log(f"Querying status for {len(self.config_paths)} event(s)")
        status_dict = {}
        for key in self._progress(list(self.config_paths.keys()), desc="Querying statuses", total=len(self.config_paths)):

            previous_status, jobid = self.read_job_status(key)
            status = self.query_job_status(key, jobid)
            status_dict.update({key: {'status': status, 'jobid': jobid}})
            self.update_job_status_file(key, {'status': status})
            self._log(f"Event {key}: {previous_status} -> {status}; jobid={jobid}", level="DEBUG")

        df = pd.DataFrame(status_dict).T
        self.file_jobs_statuses = df

        counts = df["status"].value_counts(dropna=False).to_dict() if not df.empty else {}
        self._log(f"Status query complete. Counts: {counts}")
        if self.verbose:
            print(df)

        return df

    def add_to_submitted_jobs_list(self, event):
        """Append an event name to ``submitted_jobs.txt``."""

        with open(self.submitted_jobs_list_file, "a") as file:
            file.write(f"{event}\n")
        self._log(f"Event {event}: appended to submitted jobs ledger", level="DEBUG")

    def parse_submitted_jobs_list(self):
        """Load submitted and pending event lists from the local ledger."""

        with open(self.submitted_jobs_list_file, "r") as file:
            sub_jobs = file.readlines()

        self.submitted_jobs = [item.strip("\n") for item in sub_jobs if item.strip()]
        self.pending_jobs = [item for item in self.config_paths.keys() if item not in self.submitted_jobs]

        self._log(
            f"Ledger parsed: {len(self.submitted_jobs)} submitted, {len(self.pending_jobs)} pending",
            level="DEBUG",
        )
        return sub_jobs

    def query_job_status(self, event, jobid):
        """Return the best available status for one event.

        Pending jobs are reported directly from the local ledger. Submitted jobs
        are queried from HTCondor when a cluster id is available; if the job is
        no longer in the queue, completion is inferred from the final-result
        directory.
        """
        self.parse_submitted_jobs_list()

        if event not in self.submitted_jobs:
            status = "pending"
        elif jobid is None:
            status = None
            self._log(f"Event {event}: submitted ledger entry has no jobid; falling back to result check", level="WARNING")
        else:
            status = get_condor_job_status(jobid, 0)
            self._log(f"Event {event}: Condor status for job {jobid}: {status}", level="DEBUG")

        if status is None:
            status = self.check_for_completion(event)
            self._log(f"Event {event}: inferred status from final_result directory: {status}", level="DEBUG")

        return status

    def update_job_status_file(self, event, info):
        """Merge ``info`` into an event's ``status.yaml`` file."""
        job_file = Path(os.path.dirname(self.config_paths[event])) / "status.yaml"

        if not os.path.isfile(job_file):
            with open(job_file, 'w') as file:
                yaml.dump({}, file)

        with open(job_file, 'r') as file:
            status = yaml.safe_load(file) or {}

        status.update(info)

        with open(job_file, 'w') as file:
            yaml.safe_dump(status, file, sort_keys=False)
        self._log(f"Event {event}: wrote {info} to {job_file}", level="DEBUG")

    def check_for_completion(self, event):
        """Infer completion from files in ``pe/final_result`` for an event."""

        final_results_dir = self.event_dir(event) / "pe/final_result"
        if not final_results_dir.is_dir():
            return "incomplete"

        files = os.listdir(final_results_dir)

        if not files:
            status = "incomplete"
        else:
            file = files[0]
            if 'hdf5' in file:
                status = 'completed'
            else:
                status = 'incomplete'

        return status

    def _parse_jobid_from_bilby_pipe_stdout(self, stdout):
        """Extract a Condor cluster id from bilby_pipe submission output."""
        cluster_match = re.search(r"cluster\s+(\d+)(?:\.\d+)?", stdout, re.IGNORECASE)
        if cluster_match is not None:
            return cluster_match.group(1)

        matches = re.findall(r"\b(\d+)(?:\.\d+)?\b", stdout)
        if matches:
            return matches[-1]

        raise RuntimeError(f"Could not parse Condor cluster id from bilby_pipe output:\n{stdout}")

    def submit_one_job(self, event):
        """Submit one pending event through ``bilby_pipe --submit``.

        Returns the ``subprocess.CompletedProcess`` object for new submissions;
        returns ``None`` if the event is already present in the submitted-jobs
        ledger.
        """

        self.parse_submitted_jobs_list()
        if event not in self.config_paths:
            raise KeyError(f"Unknown event {event!r}. Known events: {sorted(self.config_paths)}")

        if event not in self.submitted_jobs:
            conf_file = self.config_paths[event]
            command = ["bilby_pipe", str(conf_file), "--submit"]
            self._log(f"Event {event}: submitting with config {conf_file}")
            out = self.run_cmd(command, shell=False)
            stdout = out.stdout
            jobid = self._parse_jobid_from_bilby_pipe_stdout(stdout)
            self.add_to_submitted_jobs_list(event)
            self.update_job_status_file(event, {"jobid": jobid, "status": "submitted"})
            self._log(f"Event {event}: submitted successfully with Condor cluster id {jobid}")
            return out
        else:
            self._log(f"Event {event}: already present in submitted jobs ledger; skipping submission", level="WARNING")
            return None

    def submit_next_job(self):
        """Submit the first pending event, if one is available."""

        self.parse_submitted_jobs_list()

        if not self.pending_jobs:
            self._log("No pending jobs to submit")
            return None

        event = self.pending_jobs[0]
        self._log(f"Submitting next pending event: {event}")
        return self.submit_one_job(event)

    def submit_jobs(self, njobs=1):
        """Submit up to ``njobs`` pending events.

        Parameters
        ----------
        njobs : int, optional
            Maximum number of pending jobs to submit.

        Returns
        -------
        list[subprocess.CompletedProcess | None]
            Submission results for the attempted events.
        """

        self.parse_submitted_jobs_list()

        if njobs < 0:
            raise ValueError("njobs must be non-negative")

        to_submit = self.pending_jobs[:njobs]
        self._log(f"Submitting up to {njobs} job(s); {len(to_submit)} pending job(s) selected")

        outs = []
        for event in self._progress(to_submit, desc="Submitting jobs", total=len(to_submit)):
            outs.append(self.submit_one_job(event))

        if len(outs) < njobs:
            self._log(f"Requested {njobs} jobs but only {len(outs)} pending jobs were available", level="WARNING")

        return outs

    def load(self):
        """Discover source config files without copying or submitting jobs."""
        self._log("Loading source config discovery only; no copy, reconfigure, or submission will be performed")
        self.source_dict = self.find_bilby_configs()
        return self.source_dict

    def run(self):
        """Run the full prepare, reconfigure, ledger-load, and status-query flow."""

        self._log("Starting PERerun workflow")
        self.prepare_configs()
        self.reconfigure()
        self.parse_submitted_jobs_list()
        status = self.all_job_status()
        self._log("PERerun workflow complete")

        return status
