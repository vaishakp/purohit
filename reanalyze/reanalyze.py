"""Utilities for preparing, submitting, and monitoring bilby_pipe reruns.

``source_dir`` is the read-only tree containing original bilby_pipe INI files.
``project_dir`` is the writable tree where copied INIs, ledgers, status files,
and bilby outputs are stored.
"""

from __future__ import annotations

import getpass
import os
import re
import shutil
import subprocess
from pathlib import Path

import pandas as pd
import yaml
from tqdm import tqdm

from reanalyze.utils import get_condor_job_status


class PERerun:
    """Prepare, reconfigure, submit, and monitor bilby_pipe reruns."""

    def __init__(
        self,
        source_dir=None,
        project_dir=None,
        apx="NRSur7dq4",
        approvals=None,
        overwrite_configs=False,
        reconfigure_existing_configs=True,
        preserve_osg_settings=False,
        accounting="ligo.dev.o4.cbc.pe.bilby",
        accounting_user="auto",
        label_suffix="_p2",
        cache_config_discovery=True,
        refresh_config_cache=False,
        config_cache_file=None,
        working_dir=None,
        verbose=True,
        progress=True,
    ):
        """Create a rerun manager.

        ``working_dir`` is accepted only as a backward-compatible alias for
        ``source_dir``. Internally the source tree is always named
        ``source_dir`` to avoid confusing it with the writable project output
        directory.

        By default, copied INIs are localized for the current submit host by
        disabling OSG/container transfer settings inherited from production
        configs. Set ``preserve_osg_settings=True`` to keep those source
        settings unchanged.
        """

        if source_dir is None:
            if working_dir is None:
                raise TypeError("PERerun requires source_dir=... for the source config tree")
            source_dir = working_dir
        elif working_dir is not None and Path(source_dir) != Path(working_dir):
            raise ValueError("Pass only source_dir=...; working_dir=... is a deprecated alias")
        if project_dir is None:
            raise TypeError("PERerun requires project_dir=... for the writable output tree")

        self.source_dir = Path(source_dir)
        self.project_dir = Path(project_dir)
        self.apx = apx
        self.approvals = approvals or {}
        self.overwrite_configs = overwrite_configs
        self.reconfigure_existing_configs = reconfigure_existing_configs
        self.preserve_osg_settings = preserve_osg_settings
        self.accounting = accounting
        self.accounting_user = getpass.getuser() if accounting_user == "auto" else accounting_user
        self.label_suffix = label_suffix
        self.cache_config_discovery = cache_config_discovery
        self.refresh_config_cache = refresh_config_cache
        self.config_cache_file = Path(config_cache_file) if config_cache_file else self.project_dir / "source_config_manifest.yaml"
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

        self._log(f"Source directory: {self.source_dir}", level="DEBUG")
        self._log(f"Approximant/config token: {self.apx}", level="DEBUG")
        self._log(f"Preserve OSG/container settings: {self.preserve_osg_settings}", level="DEBUG")
        if self.cache_config_discovery:
            self._log(f"Config discovery cache: {self.config_cache_file}", level="DEBUG")

    def _log(self, message_text, level="INFO"):
        if self.verbose:
            print(f"[purohit] {level}: {message_text}")

    def _progress(self, iterable, *, desc, total=None, unit="it"):
        return tqdm(iterable, desc=desc, total=total, unit=unit, disable=not self.progress)

    def event_dir(self, event):
        return Path(os.path.dirname(self.config_paths[event]))

    def run_cmd(self, command, shell=True, capture_output=True, check=True, text=True):
        try:
            return subprocess.run(command, shell=shell, capture_output=capture_output, check=check, text=text)
        except subprocess.CalledProcessError as e:
            self._log(f"Command failed with exit code {e.returncode}: {command}", level="ERROR")
            if e.stdout:
                self._log(f"stdout:\n{e.stdout}", level="ERROR")
            if e.stderr:
                self._log(f"stderr:\n{e.stderr}", level="ERROR")
            raise

    def _config_cache_metadata(self):
        return {"source_dir": str(self.source_dir.expanduser().resolve()), "apx": self.apx}

    def _read_config_cache(self):
        if not self.cache_config_discovery or self.refresh_config_cache or not self.config_cache_file.is_file():
            if self.refresh_config_cache:
                self._log("Ignoring source config cache because refresh_config_cache=True", level="DEBUG")
            return None

        with self.config_cache_file.open("r") as handle:
            cache = yaml.safe_load(handle) or {}

        expected_metadata = self._config_cache_metadata()
        metadata = cache.get("metadata", {})
        if metadata != expected_metadata:
            self._log(
                f"Ignoring stale source config cache {self.config_cache_file}: "
                f"metadata {metadata!r} does not match {expected_metadata!r}",
                level="WARNING",
            )
            return None

        files = [Path(path) for path in cache.get("files", [])]
        missing_files = [path for path in files if not path.is_file()]
        if missing_files:
            self._log(
                f"Ignoring source config cache {self.config_cache_file}: "
                f"{len(missing_files)} cached source file(s) are missing",
                level="WARNING",
            )
            return None

        files = sorted(files)
        self._log(f"Loaded {len(files)} matching INI file(s) from cache {self.config_cache_file}")
        return files

    def _write_config_cache(self, files):
        if not self.cache_config_discovery:
            return
        self.config_cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache = {"metadata": self._config_cache_metadata(), "files": [str(path) for path in files]}
        with self.config_cache_file.open("w") as handle:
            yaml.safe_dump(cache, handle, sort_keys=False)
        self._log(f"Wrote source config cache with {len(files)} file(s): {self.config_cache_file}")

    def _scan_matching_ini_files(self):
        apx_lower = self.apx.lower()
        files = []
        event_names = set()
        scanned = matched = 0
        last_reported_match_count = last_reported_event_count = 0

        scan_iter = self.source_dir.rglob("*.ini")
        if self.progress:
            scan_iter = tqdm(scan_iter, desc="Scanning INI files", unit="file")

        for path in scan_iter:
            scanned += 1
            if apx_lower in path.name.lower():
                matched += 1
                files.append(path)
                rel_path = path.relative_to(self.source_dir)
                if rel_path.parts:
                    event_names.add(rel_path.parts[0])

            if self.progress:
                scan_iter.set_postfix(scanned=scanned, configs=matched, events=len(event_names), refresh=True)
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
        self._log(f"Searching for bilby_pipe INI files matching '*{self.apx}*.ini' under {self.source_dir}")
        if not self.source_dir.is_dir():
            raise FileNotFoundError(f"source_dir does not exist or is not a directory: {self.source_dir}")

        files = self._read_config_cache()
        if files is None:
            files = self._scan_matching_ini_files()
            self._write_config_cache(files)
        if not files:
            raise FileNotFoundError(f"No bilby_pipe ini files matching '*{self.apx}*.ini' found under {self.source_dir}")

        event_sdict = {}
        for item in files:
            rel_path = item.relative_to(self.source_dir)
            if rel_path.parts:
                event_sdict.setdefault(rel_path.parts[0], []).append(str(item))
        self._log(f"Found {len(files)} matching INI file(s)")
        self._log(f"Grouped configs into {len(event_sdict)} event(s)")

        event_dict = {}
        for event in self._progress(sorted(event_sdict), desc="Selecting configs", total=len(event_sdict)):
            event_files = sorted(event_sdict[event])
            if event in self.approvals:
                token = self.approvals[event]
                matches = [item for item in event_files if token in item]
                if matches:
                    if len(matches) > 1:
                        self._log(f"Event {event}: approval token matched {len(matches)} files; using first sorted match", level="WARNING")
                    event_file = matches[0]
                    self._log(f"Event {event}: selected approved config {event_file}", level="DEBUG")
                else:
                    event_file = event_files[-1]
                    self._log(
                        f"Event {event}: approval token {token!r} matched no configs; "
                        f"falling back to last sorted available config {event_file}. "
                        f"Available files: {event_files}",
                        level="WARNING",
                    )
            else:
                event_file = event_files[0]
                self._log(f"Event {event}: selected config {event_file}", level="DEBUG")
            event_dict[event] = event_file

        self._log(f"Selected one source config for each of {len(event_dict)} event(s)")
        return event_dict

    def copy_inis(self):
        """Copy source INIs into ``project_dir/working/<event>``.

        Existing copies are preserved unless ``overwrite_configs=True``. The
        subsequent reconfiguration step is intentionally independent of whether
        the copy step copied or skipped the file.
        """

        all_outs = {}
        config_paths = {}
        project_working_dir = self.project_dir / "working"
        project_working_dir.mkdir(parents=True, exist_ok=True)
        copied_count = skipped_count = 0

        for event, source_path in self._progress(list(self.source_dict.items()), desc="Copying configs", total=len(self.source_dict)):
            event_dir = project_working_dir / event
            event_dir.mkdir(parents=True, exist_ok=True)
            src_path = Path(source_path)
            dest_path = event_dir / src_path.name
            if dest_path.exists() and not self.overwrite_configs:
                skipped_count += 1
                all_outs[event] = "skipped_existing"
                self._log(f"Event {event}: keeping existing copied config {dest_path}")
            else:
                all_outs[event] = shutil.copy2(src_path, dest_path)
                copied_count += 1
                self._log(f"Event {event}: copied {src_path} -> {dest_path}", level="DEBUG")
            config_paths[event] = dest_path

        self._log(
            f"Config copy step complete: {copied_count} copied, {skipped_count} skipped, "
            f"{len(config_paths)} local config path(s) tracked"
        )
        return all_outs, config_paths

    def _set_ini_values(self, config_path, updates):
        path = Path(config_path)
        lines = path.read_text().splitlines()
        patterns = {key: re.compile(rf"^\s*{re.escape(key)}\s*=") for key, value in updates.items() if value is not None}
        seen = set()
        new_lines = []
        for line in lines:
            for key, pattern in patterns.items():
                if pattern.match(line):
                    new_lines.append(f"{key}={updates[key]}")
                    seen.add(key)
                    break
            else:
                new_lines.append(line)
        for key, value in updates.items():
            if value is not None and key not in seen:
                new_lines.append(f"{key}={value}")
        path.write_text("\n".join(new_lines) + "\n")

    def _remove_ini_values(self, config_path, keys):
        path = Path(config_path)
        lines = path.read_text().splitlines()
        patterns = {key: re.compile(rf"^\s*{re.escape(key)}\s*=") for key in keys}
        removed = []
        new_lines = []
        for line in lines:
            matched_key = next((key for key, pattern in patterns.items() if pattern.match(line)), None)
            if matched_key is None:
                new_lines.append(line)
            else:
                removed.append(matched_key)
        if removed:
            path.write_text("\n".join(new_lines) + "\n")
            removed_keys = ", ".join(sorted(set(removed)))
            self._log(f"Removed source INI setting(s) from {path}: {removed_keys}", level="DEBUG")

    def _active_conda_env_name(self):
        """Return the active Conda-compatible environment name, if one is visible.

        Prefer ``CONDA_DEFAULT_ENV`` because it is the user-facing environment
        name. Fall back to the basename of ``CONDA_PREFIX`` for shells that only
        expose the resolved environment prefix. If neither is available, return
        ``None`` so copied source ``conda-env`` entries can be removed instead
        of pointing at a stale production environment.
        """

        env_name = os.environ.get("CONDA_DEFAULT_ENV")
        if env_name:
            return env_name
        conda_prefix = os.environ.get("CONDA_PREFIX")
        if conda_prefix:
            return Path(conda_prefix).name
        return None

    def reconfigure_one_ini(self, event):
        """Edit one copied project-local bilby_pipe INI for resubmission."""

        config_path = Path(self.config_paths[event])
        outdir = config_path.parent / "pe"
        webdir = self.project_dir / "webdir"
        analysis_executable = shutil.which("bilby_pipe")
        if analysis_executable is None:
            raise FileNotFoundError("Could not find 'bilby_pipe' on PATH. Activate the intended bilby_pipe environment first.")
        conda_env = self._active_conda_env_name()

        self._log(f"Event {event}: reconfiguring {config_path}")
        self._log(f"Event {event}: outdir={outdir}", level="DEBUG")
        self._log(f"Event {event}: webdir={webdir}", level="DEBUG")
        self._log(f"Event {event}: analysis-executable={analysis_executable}", level="DEBUG")
        if conda_env:
            self._log(f"Event {event}: conda-env={conda_env}", level="DEBUG")
        else:
            self._log(f"Event {event}: no active conda-env detected; removing copied conda-env key", level="DEBUG")
        if self.accounting is not None:
            self._log(f"Event {event}: accounting={self.accounting}", level="DEBUG")
        if self.accounting_user is not None:
            self._log(f"Event {event}: accounting-user={self.accounting_user}", level="DEBUG")

        updates = {
            "label": f"{event}{self.label_suffix}",
            "outdir": str(outdir),
            "webdir": str(webdir),
            "accounting": self.accounting,
            "accounting-user": self.accounting_user,
            "request-memory": "8",
            "request-disk": "16",
            "analysis-executable": analysis_executable,
            "conda-env": conda_env,
            "submit": "condor",
            "sampler-kwargs": "{'nlive': 2000, 'naccept': 60, 'check_point_plot': True, 'check_point_delta_t': 1800, 'print_method': 'interval-60', 'sample': 'acceptance-walk', 'npool': 16, 'dlogz': 0.01}",
        }
        if not self.preserve_osg_settings:
            updates.update({"osg": "False", "transfer-files": "False", "scheduler-env": "None"})
        if self.apx == "NRSur7dq4":
            updates["additional-transfer-paths"] = "[/scratch/lalsimulation/NRSur7dq4_v1.0.h5]"
        self._set_ini_values(config_path, updates)
        keys_to_remove = [] if conda_env else ["conda-env"]
        if not self.preserve_osg_settings:
            keys_to_remove.append("container")
        self._remove_ini_values(config_path, keys_to_remove)
        if not self.preserve_osg_settings:
            self._log(
                f"Event {event}: localized INI by disabling OSG/container transfer settings",
                level="DEBUG",
            )

        text = config_path.read_text()
        text = re.sub(
            r"a_1\s*=\s*Uniform\s*\(\s*name\s*=\s*'a_1',\s*minimum\s*=\s*0,\s*maximum\s*=\s*0\.99\s*\)",
            "a_1 = PowerLaw(name='a_1', minimum=0, maximum=1, alpha=2)",
            text,
        )
        text = re.sub(
            r"\s*a_2\s*=\s*Uniform\s*\(\s*name\s*=\s*'a_2',\s*minimum\s*=\s*0,\s*maximum\s*=\s*0\.99\s*\)",
            "a_2 = PowerLaw(name='a_2', minimum=0, maximum=1, alpha=2)",
            text,
        )
        config_path.write_text(text)
        self._log(f"Event {event}: reconfiguration complete")

    def prepare_configs(self, source_dir=None, apx=None, working_dir=None):
        if source_dir is not None:
            self.source_dir = Path(source_dir)
        elif working_dir is not None:
            self.source_dir = Path(working_dir)
        if apx is not None:
            self.apx = apx
        self._log("Preparing local config files")
        self.source_dict = self.find_bilby_configs()
        outs, self.config_paths = self.copy_inis()
        self._log(f"Prepared {len(self.config_paths)} local config path(s)")
        return outs, self.config_paths

    def reconfigure(self):
        if self.resume and not self.reconfigure_existing_configs:
            self._log("Resume mode detected and reconfigure_existing_configs=False; skipping automatic INI reconfiguration", level="WARNING")
            return
        self._log(f"Reconfiguring {len(self.config_paths)} copied INI file(s)")
        for event, _config_path in self._progress(list(self.config_paths.items()), desc="Reconfiguring configs", total=len(self.config_paths)):
            self.reconfigure_one_ini(event)
        self._log("INI reconfiguration step complete")

    def read_job_status(self, event):
        job_file = self.event_dir(event) / "status.yaml"
        if not job_file.is_file():
            return "pending", None
        info = yaml.safe_load(job_file.read_text()) or {}
        return info.get("status", "unknown"), info.get("jobid")

    def all_job_status(self):
        self._log(f"Querying status for {len(self.config_paths)} event(s)")
        status_dict = {}
        for event in self._progress(list(self.config_paths.keys()), desc="Querying statuses", total=len(self.config_paths)):
            previous_status, jobid = self.read_job_status(event)
            status = self.query_job_status(event, jobid)
            status_dict[event] = {"status": status, "jobid": jobid}
            self.update_job_status_file(event, {"status": status})
            self._log(f"Event {event}: {previous_status} -> {status}; jobid={jobid}", level="DEBUG")
        df = pd.DataFrame(status_dict).T
        self.file_jobs_statuses = df
        counts = df["status"].value_counts(dropna=False).to_dict() if not df.empty else {}
        self._log(f"Status query complete. Counts: {counts}")
        if self.verbose:
            print(df)
        return df

    def add_to_submitted_jobs_list(self, event):
        with self.submitted_jobs_list_file.open("a") as file:
            file.write(f"{event}\n")
        self._log(f"Event {event}: appended to submitted jobs ledger", level="DEBUG")

    def parse_submitted_jobs_list(self):
        with self.submitted_jobs_list_file.open("r") as file:
            sub_jobs = file.readlines()
        self.submitted_jobs = [item.strip("\n") for item in sub_jobs if item.strip()]
        self.pending_jobs = [item for item in self.config_paths.keys() if item not in self.submitted_jobs]
        self._log(f"Ledger parsed: {len(self.submitted_jobs)} submitted, {len(self.pending_jobs)} pending", level="DEBUG")
        return sub_jobs

    def query_job_status(self, event, jobid):
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
        job_file = self.event_dir(event) / "status.yaml"
        if not job_file.is_file():
            job_file.write_text(yaml.safe_dump({}))
        status = yaml.safe_load(job_file.read_text()) or {}
        status.update(info)
        job_file.write_text(yaml.safe_dump(status, sort_keys=False))
        self._log(f"Event {event}: wrote {info} to {job_file}", level="DEBUG")

    def check_for_completion(self, event):
        final_results_dir = self.event_dir(event) / "pe" / "final_result"
        if not final_results_dir.is_dir():
            return "incomplete"
        files = os.listdir(final_results_dir)
        if not files:
            return "incomplete"
        return "completed" if "hdf5" in files[0] else "incomplete"

    def _parse_jobid_from_bilby_pipe_stdout(self, stdout):
        cluster_match = re.search(r"cluster\s+(\d+)(?:\.\d+)?", stdout, re.IGNORECASE)
        if cluster_match is not None:
            return cluster_match.group(1)
        matches = re.findall(r"\b(\d+)(?:\.\d+)?\b", stdout)
        if matches:
            return matches[-1]
        raise RuntimeError(f"Could not parse Condor cluster id from bilby_pipe output:\n{stdout}")

    def submit_one_job(self, event):
        self.parse_submitted_jobs_list()
        if event not in self.config_paths:
            raise KeyError(f"Unknown event {event!r}. Known events: {sorted(self.config_paths)}")
        if event in self.submitted_jobs:
            self._log(f"Event {event}: already present in submitted jobs ledger; skipping submission", level="WARNING")
            return None

        conf_file = self.config_paths[event]
        command = ["bilby_pipe", str(conf_file), "--submit"]
        self._log(f"Event {event}: submitting with config {conf_file}")
        out = self.run_cmd(command, shell=False)
        jobid = self._parse_jobid_from_bilby_pipe_stdout(out.stdout)
        self.add_to_submitted_jobs_list(event)
        self.update_job_status_file(event, {"jobid": jobid, "status": "submitted"})
        self._log(f"Event {event}: submitted successfully with Condor cluster id {jobid}")
        return out

    def submit_next_job(self):
        self.parse_submitted_jobs_list()
        if not self.pending_jobs:
            self._log("No pending jobs to submit")
            return None
        event = self.pending_jobs[0]
        self._log(f"Submitting next pending event: {event}")
        return self.submit_one_job(event)

    def submit_jobs(self, njobs=1):
        self.parse_submitted_jobs_list()
        if njobs < 0:
            raise ValueError("njobs must be non-negative")
        to_submit = self.pending_jobs[:njobs]
        self._log(f"Submitting up to {njobs} job(s); {len(to_submit)} pending job(s) selected")
        outs = [self.submit_one_job(event) for event in self._progress(to_submit, desc="Submitting jobs", total=len(to_submit))]
        if len(outs) < njobs:
            self._log(f"Requested {njobs} jobs but only {len(outs)} pending jobs were available", level="WARNING")
        return outs

    def load(self):
        self._log("Loading source config discovery only; no copy, reconfigure, or submission will be performed")
        self.source_dict = self.find_bilby_configs()
        return self.source_dict

    def run(self):
        self._log("Starting PERerun workflow")
        self.prepare_configs()
        self.reconfigure()
        self.parse_submitted_jobs_list()
        status = self.all_job_status()
        self._log("PERerun workflow complete")
        return status
