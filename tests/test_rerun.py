from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from reanalyze.reanalyze import PERerun


def _make_config(path: Path, approximant: str = "NRSur7dq4") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                f"label=old-{approximant}",
                "accounting-user=old-user",
                "outdir=old-outdir",
                "webdir=old-webdir",
                "request-memory=4",
                "request-disk=4",
                "analysis-executable=/old/bin/bilby_pipe_analysis",
                "submit=False",
                "additional-transfer-paths=[]",
                "sampler-kwargs={'nlive': 100}",
                "a_1 = Uniform(name='a_1', minimum=0, maximum=0.99)",
                "a_2 = Uniform(name='a_2', minimum=0, maximum=0.99)",
                "",
            ]
        )
    )


def test_find_bilby_configs_uses_relative_event_directory(tmp_path):
    working_dir = tmp_path / "deep" / "machine" / "working"
    cfg = working_dir / "S240001a" / "nested" / "bilby-NRSur7dq4.ini"
    _make_config(cfg)

    rerun = PERerun(working_dir=working_dir, project_dir=tmp_path / "project")

    assert rerun.find_bilby_configs() == {"S240001a": str(cfg)}


def test_find_bilby_configs_honors_approval_token(tmp_path):
    working_dir = tmp_path / "working"
    rejected = working_dir / "S240001a" / "bilby-NRSur7dq4-old.ini"
    approved = working_dir / "S240001a" / "bilby-NRSur7dq4-approved.ini"
    _make_config(rejected)
    _make_config(approved)

    rerun = PERerun(
        working_dir=working_dir,
        project_dir=tmp_path / "project",
        approvals={"S240001a": "approved"},
    )

    assert rerun.find_bilby_configs() == {"S240001a": str(approved)}


def test_find_bilby_configs_raises_for_missing_approval(tmp_path):
    working_dir = tmp_path / "working"
    cfg = working_dir / "S240001a" / "bilby-NRSur7dq4.ini"
    _make_config(cfg)

    rerun = PERerun(
        working_dir=working_dir,
        project_dir=tmp_path / "project",
        approvals={"S240001a": "does-not-exist"},
    )

    with pytest.raises(ValueError, match="No approved config file"):
        rerun.find_bilby_configs()


def test_copy_inis_copies_on_fresh_project(tmp_path):
    source = tmp_path / "working" / "S240001a" / "bilby-NRSur7dq4.ini"
    _make_config(source)
    project_dir = tmp_path / "project"

    rerun = PERerun(working_dir=tmp_path / "working", project_dir=project_dir)
    rerun.source_dict = {"S240001a": str(source)}

    copied, config_paths = rerun.copy_inis()

    dest = project_dir / "working" / "S240001a" / source.name
    assert config_paths == {"S240001a": dest}
    assert Path(copied["S240001a"]) == dest
    assert dest.read_text() == source.read_text()


def test_read_job_status_handles_empty_yaml(tmp_path):
    rerun = PERerun(working_dir=tmp_path / "working", project_dir=tmp_path / "project")
    event_dir = tmp_path / "project" / "working" / "S240001a"
    event_dir.mkdir(parents=True)
    cfg = event_dir / "config.ini"
    cfg.touch()
    (event_dir / "status.yaml").write_text("")
    rerun.config_paths = {"S240001a": cfg}

    assert rerun.read_job_status("S240001a") == ("unknown", None)


def test_read_job_status_reads_status_and_jobid(tmp_path):
    rerun = PERerun(working_dir=tmp_path / "working", project_dir=tmp_path / "project")
    event_dir = tmp_path / "project" / "working" / "S240001a"
    event_dir.mkdir(parents=True)
    cfg = event_dir / "config.ini"
    cfg.touch()
    (event_dir / "status.yaml").write_text(yaml.safe_dump({"status": "running", "jobid": "12345"}))
    rerun.config_paths = {"S240001a": cfg}

    assert rerun.read_job_status("S240001a") == ("running", "12345")


def test_check_for_completion_handles_missing_final_result(tmp_path):
    rerun = PERerun(working_dir=tmp_path / "working", project_dir=tmp_path / "project")
    event_dir = tmp_path / "project" / "working" / "S240001a"
    event_dir.mkdir(parents=True)
    cfg = event_dir / "config.ini"
    cfg.touch()
    rerun.config_paths = {"S240001a": cfg}

    assert rerun.check_for_completion("S240001a") == "incomplete"


def test_check_for_completion_detects_hdf5_result(tmp_path):
    rerun = PERerun(working_dir=tmp_path / "working", project_dir=tmp_path / "project")
    final_result = tmp_path / "project" / "working" / "S240001a" / "pe" / "final_result"
    final_result.mkdir(parents=True)
    (final_result / "result.hdf5").touch()
    cfg = tmp_path / "project" / "working" / "S240001a" / "config.ini"
    cfg.touch()
    rerun.config_paths = {"S240001a": cfg}

    assert rerun.check_for_completion("S240001a") == "completed"


def test_submit_next_job_returns_none_when_no_pending_jobs(tmp_path):
    rerun = PERerun(working_dir=tmp_path / "working", project_dir=tmp_path / "project")
    cfg = tmp_path / "project" / "working" / "S240001a" / "config.ini"
    cfg.parent.mkdir(parents=True)
    cfg.touch()
    rerun.config_paths = {"S240001a": cfg}
    rerun.submitted_jobs_list_file.write_text("S240001a\n")

    assert rerun.submit_next_job() is None


def test_submit_jobs_rejects_negative_njobs(tmp_path):
    rerun = PERerun(working_dir=tmp_path / "working", project_dir=tmp_path / "project")
    rerun.config_paths = {}

    with pytest.raises(ValueError, match="njobs must be non-negative"):
        rerun.submit_jobs(-1)


def test_parse_jobid_prefers_condor_cluster_id(tmp_path):
    rerun = PERerun(working_dir=tmp_path / "working", project_dir=tmp_path / "project")

    assert rerun._parse_jobid_from_bilby_pipe_stdout(
        "Submitting job\n1 job(s) submitted to cluster 12345.\n"
    ) == "12345"


def test_submit_one_job_records_jobid_and_status(tmp_path, monkeypatch):
    rerun = PERerun(working_dir=tmp_path / "working", project_dir=tmp_path / "project")
    cfg = tmp_path / "project" / "working" / "S240001a" / "config.ini"
    cfg.parent.mkdir(parents=True)
    cfg.touch()
    rerun.config_paths = {"S240001a": cfg}

    completed = subprocess.CompletedProcess(
        args=["bilby_pipe", str(cfg), "--submit"],
        returncode=0,
        stdout="Submitting job\n1 job(s) submitted to cluster 12345.\n",
        stderr="",
    )
    monkeypatch.setattr(rerun, "run_cmd", lambda *args, **kwargs: completed)

    out = rerun.submit_one_job("S240001a")

    assert out is completed
    assert rerun.submitted_jobs_list_file.read_text() == "S240001a\n"
    status = yaml.safe_load((cfg.parent / "status.yaml").read_text())
    assert status == {"jobid": "12345", "status": "submitted"}


def test_all_job_status_persists_queried_status(tmp_path, monkeypatch):
    rerun = PERerun(working_dir=tmp_path / "working", project_dir=tmp_path / "project")
    cfg = tmp_path / "project" / "working" / "S240001a" / "config.ini"
    cfg.parent.mkdir(parents=True)
    cfg.touch()
    rerun.config_paths = {"S240001a": cfg}
    rerun.submitted_jobs_list_file.write_text("S240001a\n")
    rerun.update_job_status_file("S240001a", {"jobid": "12345"})
    monkeypatch.setattr("reanalyze.reanalyze.get_condor_job_status", lambda jobid, procid: "running")

    df = rerun.all_job_status()

    assert df.loc["S240001a", "status"] == "running"
    status = yaml.safe_load((cfg.parent / "status.yaml").read_text())
    assert status["status"] == "running"
