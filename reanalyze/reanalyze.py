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
        config file when multiple matching INI files exist for that event.

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
                 overwrite_configs=False):

        self.working_dir = Path(working_dir)
        self.project_dir = Path(project_dir)
        self.apx = apx
        self.approvals = approvals or {}
        self.overwrite_configs = overwrite_configs

        self.project_dir.mkdir(parents=True, exist_ok=True)

        os.chdir(self.project_dir)

        self.submitted_jobs_list_file = self.project_dir / "submitted_jobs.txt"
        self.resume = self.submitted_jobs_list_file.is_file()

        if not self.resume:
            self.submitted_jobs_list_file.touch()

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
            print(f"Command:\n {command}\nfailed!")
            print(f"Exit code: {e.returncode}")
            if e.stdout:
                print(f"stdout:\n{e.stdout}")
            if e.stderr:
                print(f"stderr:\n{e.stderr}")
            raise

        return out

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
        ValueError
            If an approval token is supplied but matches no files for an event.
        """

        message("Running find...", message_verbosity=2)
        print("Running find")

        if not self.working_dir.is_dir():
            raise FileNotFoundError(f"working_dir does not exist or is not a directory: {self.working_dir}")

        apx_lower = self.apx.lower()
        files = sorted(
            path for path in self.working_dir.rglob("*.ini")
            if apx_lower in path.name.lower()
        )

        if not files:
            raise FileNotFoundError(
                f"No bilby_pipe ini files matching '*{self.apx}*.ini' found under {self.working_dir}"
            )

        event_sdict = {}

        message("Parsing event names", message_verbosity=2)
        for item in files:
            rel_path = item.relative_to(self.working_dir)
            if not rel_path.parts:
                continue
            event_name = rel_path.parts[0]
            event_sdict.setdefault(event_name, []).append(str(item))

        event_dict = {}

        message("Finding configs", message_verbosity=2)

        for event in tqdm(sorted(event_sdict)):
            event_files = sorted(event_sdict[event])

            if event in self.approvals:
                tfile = self.approvals[event]
                fil_files = [item for item in event_files if tfile in item]
                if not fil_files:
                    raise ValueError(
                        f"No approved config file for event {event!r} matched approval token {tfile!r}. "
                        f"Available files: {event_files}"
                    )
                if len(fil_files) > 1:
                    message(f"Found {len(fil_files)} approved files for {event}", message_verbosity=2)
                event_file = fil_files[0]

                message(f"Choosing {event_file} for {event}", message_verbosity=2)
            else:
                event_file = event_files[0]

            event_dict.update({event: event_file})

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

        for key, val in self.source_dict.items():
            event_dir = working_dir / key
            event_dir.mkdir(parents=True, exist_ok=True)

            src_path = Path(val)
            filename = src_path.name
            dest_path = event_dir / filename

            if not self.resume:
                if dest_path.exists() and not self.overwrite_configs:
                    print(f"Skipping existing config for {key}: {dest_path}")
                    all_outs.update({key: "skipped_existing"})
                else:
                    copied_path = shutil.copy2(src_path, dest_path)
                    all_outs.update({key: copied_path})

            config_paths.update({key: dest_path})

        return all_outs, config_paths

    def reconfigure_one_ini(self, event):
        """Edit one copied bilby_pipe INI for resubmission.

        The current implementation updates the label, accounting user, output
        directory, web directory, resource requests, analysis executable,
        submission backend, selected spin priors, and sampler kwargs. For
        ``NRSur7dq4`` runs it also writes the NRSur7dq4 HDF5 transfer path.
        """

        to_change = ["label",
                    "accounting-user",
                    "outdir",
                    "webdir",
                    "request-memory",
                    "request-disk",
                    "analysis-executable",
                    "prior-dict",
                    "sampler-kwargs"
                    ]

        config_path = self.config_paths[event]
        print(config_path)

        user = getpass.getuser()

        webdir = self.project_dir / "webdir"

        outdir = f"{os.path.dirname(config_path)}/pe"
        print(outdir)
        command = f"sed -i '/^label/c\\label={event}_p2' {config_path}"
        out = self.run_cmd(command, shell=True,  capture_output=True, text=True)
        command = f"sed -i '/^accounting-user/c\\accounting-user={user}' {config_path}"
        out = self.run_cmd(command, shell=True,  capture_output=True, text=True)
        command = f"sed -i '/^outdir/c\\outdir={outdir}' {config_path}"
        out = self.run_cmd(command, shell=True,  capture_output=True, text=True)
        command = f"sed -i '/^webdir/c\\webdir={webdir}' {config_path}"
        out = self.run_cmd(command, shell=True,  capture_output=True, text=True)
        command = f"sed -i '/^request-memory=/c\\request-memory=8' {config_path}"
        out = self.run_cmd(command, shell=True,  capture_output=True, text=True)
        command = f"sed -i '/^request-disk/c\\request-disk=16' {config_path}"
        out = self.run_cmd(command, shell=True,  capture_output=True, text=True)
        bilby_pipe_analysis_path = shutil.which("bilby_pipe_analysis")
        if bilby_pipe_analysis_path is None:
            raise FileNotFoundError(
                "Could not find 'bilby_pipe_analysis' on PATH. Activate the intended bilby_pipe environment first."
            )
        command = f"sed -i '/^analysis-executable=/c\\analysis-executable={bilby_pipe_analysis_path}' {config_path}"
        out = self.run_cmd(command, shell=True,  capture_output=True, text=True)
        command = f"sed -i '/^submit=/c\\submit=condor' {config_path}"
        out = self.run_cmd(command, shell=True,  capture_output=True, text=True)
        if self.apx == 'NRSur7dq4':
            command = f"sed -i '/^additional-transfer-paths=/c\\additional-transfer-paths=[\/scratch\/lalsimulation/NRSur7dq4_v1.0.h5]' {config_path}"
            out = self.run_cmd(command, shell=True,  capture_output=True, text=True)

        cmd = [
            "sed", "-Ei",
            r"s/a_1[[:space:]]*=[[:space:]]*Uniform[[:space:]]*\([[:space:]]*name[[:space:]]*=[[:space:]]*'a_1',[[:space:]]*minimum[[:space:]]*=[[:space:]]*0,[[:space:]]*maximum[[:space:]]*=[[:space:]]*0\.99[[:space:]]*\)/a_1 = PowerLaw(name='a_1', minimum=0, maximum=1, alpha=2)/g",
            config_path
            ]

        out = self.run_cmd(cmd, shell=False)
        # print(out)
        cmd = [
            "sed", "-Ei",
            r"s/[[:space:]]*a_2[[:space:]]*=[[:space:]]*Uniform[[:space:]]*\([[:space:]]*name[[:space:]]*=[[:space:]]*'a_2',[[:space:]]*minimum[[:space:]]*=[[:space:]]*0,[[:space:]]*maximum[[:space:]]*=[[:space:]]*0\.99[[:space:]]*\)/ a_2 = PowerLaw(name='a_2', minimum=0, maximum=1, alpha=2)/g",
            config_path
            ]

        out = self.run_cmd(cmd, shell=False)
        sampler_kwargs = "sampler-kwargs={'nlive': 2000, 'naccept': 60, 'check_point_plot': True, 'check_point_delta_t': 1800, 'print_method': 'interval-60', 'sample': 'acceptance-walk', 'npool': 16, 'dlogz': 0.01}"
        command = f"sed -i '/^sampler-kwargs/c\\{sampler_kwargs}' {config_path}"
        out = self.run_cmd(command)

    def prepare_configs(self,
                        working_dir="/home/pe.o4/GWTC4/working",
                        apx='NRSur7dq4'):
        """Discover source configs and copy them into the local project tree.

        The ``working_dir`` and ``apx`` arguments are retained for backward
        compatibility but are not currently used; the object attributes set at
        initialization determine the search path and approximant token.
        """

        message("Running find configs", message_verbosity=2)
        self.source_dict = self.find_bilby_configs()
        message("Copying inis", message_verbosity=2)
        outs, config_paths = self.copy_inis()
        self.config_paths = config_paths

    def reconfigure(self):
        """Reconfigure copied INI files for a fresh project.

        Existing projects are treated as resume operations and are not
        reconfigured automatically.
        """
        if not self.resume:
            for event, config_path in self.config_paths.items():
                self.reconfigure_one_ini(event)

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

        status_dict = {}
        for key in self.config_paths.keys():

            status, jobid = self.read_job_status(key)
            status = self.query_job_status(key, jobid)
            status_dict.update({key: {'status': status, 'jobid': jobid}})
            self.update_job_status_file(key, {'status': status})

        df = pd.DataFrame(status_dict).T
        print(df)

        self.file_jobs_statuses = df

        return df

    def add_to_submitted_jobs_list(self, event):
        """Append an event name to ``submitted_jobs.txt``."""

        with open(self.submitted_jobs_list_file, "a") as file:
        # This writes the list of strings to the file.
            file.write(f"{event}\n")

    def parse_submitted_jobs_list(self):
        """Load submitted and pending event lists from the local ledger."""

        with open(self.submitted_jobs_list_file, "r") as file:
        # This writes the list of strings to the file.
            sub_jobs = file.readlines()

        self.submitted_jobs = [item.strip("\n") for item in sub_jobs if item.strip()]
        self.pending_jobs = [item for item in self.config_paths.keys() if item not in self.submitted_jobs]

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
        else:
            status = get_condor_job_status(jobid, 0)
            print(jobid, status)

        if status is None:
            status = self.check_for_completion(event)

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
        if event not in self.submitted_jobs:
            conf_file = self.config_paths[event]
            command = ["bilby_pipe", str(conf_file), "--submit"]
            out = self.run_cmd(command, shell=False)
            stdout = out.stdout
            jobid = self._parse_jobid_from_bilby_pipe_stdout(stdout)
            self.add_to_submitted_jobs_list(event)
            self.update_job_status_file(event, {"jobid": jobid, "status": "submitted"})
            print(f"Submitted {event} with jobid {jobid}")
            return out
        else:
            print(f"Job {event} previosly submitted")
            return None

    def submit_next_job(self):
        """Submit the first pending event, if one is available."""

        self.parse_submitted_jobs_list()

        if not self.pending_jobs:
            print("No pending jobs to submit")
            return None

        event = self.pending_jobs[0]
        out = self.submit_one_job(event)
        print(f"Submitted {event}")
        return out

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

        outs = []
        for event in self.pending_jobs[:njobs]:
            outs.append(self.submit_one_job(event))
            print(f"Submitted {event}")

        if len(outs) < njobs:
            print(f"Requested {njobs} jobs but only {len(outs)} pending jobs were available")

        return outs

    def load(self):
        """Discover source config files without copying or submitting jobs."""
        self.source_dict = self.find_bilby_configs()
        return self.source_dict

    def run(self):
        """Run the full prepare, reconfigure, ledger-load, and status-query flow."""

        self.prepare_configs()
        self.reconfigure()
        self.parse_submitted_jobs_list()
        status = self.all_job_status()

        return status
