from __future__ import annotations

import json
from pathlib import Path
import shutil

import yaml

from reanalyze import remote_import
from reanalyze.host_profiles import HostProfile, HostProfiles
from reanalyze.remote_import import (
    event_data_path,
    home_relative_path,
    parse_ini_dependencies_text,
    split_dependency_candidates,
)
from reanalyze.static_manager import find_event_config


def test_home_relative_path_preserves_suffix_after_home():
    rel = home_relative_path("/home/source/Projects/ligo/input/H1.dat", Path("/home/source"))
    assert rel.as_posix() == "Projects/ligo/input/H1.dat"


def test_event_data_path_uses_event_local_home_relative_layout(tmp_path):
    target = event_data_path(tmp_path / "proj", "S1", "/home/source/Projects/ligo/input/H1.dat", Path("/home/source"))
    assert target == tmp_path / "proj" / "working" / "S1" / "data" / "home-relative" / "Projects" / "ligo" / "input" / "H1.dat"


def test_parse_ini_dependencies_skips_preserved_roots():
    text = """
psd_file = /home/source/psd/H1.dat
basis_file = /cvmfs/example/basis.hdf5
not_a_path = hello
"""
    deps = parse_ini_dependencies_text(text)
    assert len(deps) == 1
    assert deps[0].key == "psd_file"
    assert deps[0].source_path == "/home/source/psd/H1.dat"
    assert deps[0].ini_path == "/home/source/psd/H1.dat"
    assert deps[0].kind == "psd"


def test_split_dependency_candidates_expands_detector_path_map():
    value = (
        "H1:/home/pe.o4/GWTC5-HLV/project/working/S240413p/get-calibration-ligo-L1/calibration/H1.txt,"
        "L1:/home/pe.o4/GWTC5-HLV/project/working/S240413p/get-calibration-ligo-L1/calibration/L1.txt,"
        "V1:/home/pe.o4/GWTC5-HLV/project/working/S240413p/get-calibration-virgo/calibration/V1.txt,"
    )

    assert split_dependency_candidates(value) == [
        "/home/pe.o4/GWTC5-HLV/project/working/S240413p/get-calibration-ligo-L1/calibration/H1.txt",
        "/home/pe.o4/GWTC5-HLV/project/working/S240413p/get-calibration-ligo-L1/calibration/L1.txt",
        "/home/pe.o4/GWTC5-HLV/project/working/S240413p/get-calibration-virgo/calibration/V1.txt",
    ]


def test_parse_ini_dependencies_expands_calibration_detector_path_map():
    text = """
calibration_model = H1:/home/source/calibration/H1.txt,L1:/home/source/calibration/L1.txt,V1:/home/source/calibration/V1.txt,
"""

    deps = parse_ini_dependencies_text(text)

    assert [dep.source_path for dep in deps] == [
        "/home/source/calibration/H1.txt",
        "/home/source/calibration/L1.txt",
        "/home/source/calibration/V1.txt",
    ]
    assert [dep.ini_path for dep in deps] == [
        "/home/source/calibration/H1.txt",
        "/home/source/calibration/L1.txt",
        "/home/source/calibration/V1.txt",
    ]
    assert {dep.kind for dep in deps} == {"calibration"}


def test_remote_materialize_copies_data_files_and_reconfigures_target_ini(tmp_path, monkeypatch):
    source_home = tmp_path / "source_home"
    target_home = tmp_path / "target_home"
    target_project = target_home / "project"
    source_ini = source_home / "catalog" / "S1" / "bilby-IMRPhenomXPHM.ini"
    psd = source_home / "inputs" / "H1_psd.dat"
    relative_data = source_ini.parent / "relative_data.dat"
    psd.parent.mkdir(parents=True)
    source_ini.parent.mkdir(parents=True)
    psd.write_text("psd\n")
    relative_data.write_text("relative\n")
    source_ini.write_text(
        "\n".join(
            [
                "label=old-label",
                f"outdir={source_home}/old/out",
                f"webdir={source_home}/old/web",
                "accounting-user=old-user",
                "request-memory=4",
                "request-disk=4",
                "analysis-executable=/old/bin/bilby_pipe",
                "submit=False",
                "container=/old/container.sif",
                "osg=True",
                "transfer-files=True",
                f"psd_file={psd}",
                "data_file=relative_data.dat",
                "basis_file=/cvmfs/example/basis.hdf5",
                "a_1 = Uniform(name='a_1', minimum=0, maximum=0.99)",
                "a_2 = Uniform(name='a_2', minimum=0, maximum=0.99)",
                "",
            ]
        )
    )

    def fake_rsync_pull(_source_host, source_path, target_path, rsync_args=None):
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path(source_path), target_path)

    monkeypatch.setattr(remote_import, "rsync_pull", fake_rsync_pull)
    monkeypatch.setattr(remote_import.shutil, "which", lambda executable: "/target/env/bin/bilby_pipe")
    monkeypatch.delenv("CONDA_DEFAULT_ENV", raising=False)
    monkeypatch.delenv("CONDA_PREFIX", raising=False)

    result = remote_import.materialize_event(
        event="S1",
        source_ini=str(source_ini),
        source_host=HostProfile(name="cit", ssh="cit", home=source_home),
        target_host=HostProfile(name="gwave", home=target_home, project_dir=target_project),
        target_project_dir=target_project,
        apx="IMRPhenomXPHM",
        accounting="ligo.dev.test",
        accounting_user="alice",
        label_suffix="_rerun",
        verbose=False,
    )

    copied_psd = event_data_path(target_project, "S1", str(psd), source_home)
    copied_relative = event_data_path(target_project, "S1", str(relative_data), source_home)
    assert copied_psd.read_text() == "psd\n"
    assert copied_relative.read_text() == "relative\n"

    text = result.submit_ini.read_text()
    assert str(psd) not in text
    assert "data_file=relative_data.dat" not in text
    assert f"psd_file={copied_psd}" in text
    assert f"data_file={copied_relative}" in text
    assert "basis_file=/cvmfs/example/basis.hdf5" in text
    assert f"outdir={target_project / 'working' / 'S1' / 'pe'}" in text
    assert f"webdir={target_project / 'webdir'}" in text
    assert "label=S1_rerun" in text
    assert "accounting=ligo.dev.test" in text
    assert "accounting-user=alice" in text
    assert "request-memory=8" in text
    assert "request-disk=16" in text
    assert "analysis-executable=/target/env/bin/bilby_pipe" in text
    assert "submit=condor" in text
    assert "osg=False" in text
    assert "transfer-files=False" in text
    assert "scheduler-env=None" in text
    assert "container=" not in text
    assert "conda-env=" not in text
    assert "a_1 = PowerLaw" in text
    assert "a_2 = PowerLaw" in text

    manifest = json.loads(result.manifest_path.read_text())
    assert len(manifest["dependencies"]) == 2
    assert {item["ini_path"] for item in manifest["dependencies"]} == {str(psd), "relative_data.dat"}


def test_remote_materialize_can_preserve_osg_settings(tmp_path, monkeypatch):
    source_home = tmp_path / "source_home"
    target_home = tmp_path / "target_home"
    target_project = target_home / "project"
    source_ini = source_home / "catalog" / "S1" / "bilby-IMRPhenomXPHM.ini"
    source_ini.parent.mkdir(parents=True)
    source_ini.write_text(
        "\n".join(
            [
                "label=old-label",
                "container=/old/container.sif",
                "osg=True",
                "transfer-files=True",
                "",
            ]
        )
    )

    def fake_rsync_pull(_source_host, source_path, target_path, rsync_args=None):
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path(source_path), target_path)

    monkeypatch.setattr(remote_import, "rsync_pull", fake_rsync_pull)
    monkeypatch.setattr(remote_import.shutil, "which", lambda executable: "/target/env/bin/bilby_pipe")

    result = remote_import.materialize_event(
        event="S1",
        source_ini=str(source_ini),
        source_host=HostProfile(name="cit", ssh="cit", home=source_home),
        target_host=HostProfile(name="gwave", home=target_home, project_dir=target_project),
        target_project_dir=target_project,
        apx="IMRPhenomXPHM",
        preserve_osg_settings=True,
        verbose=False,
    )

    text = result.submit_ini.read_text()
    assert "container=/old/container.sif" in text
    assert "osg=True" in text
    assert "transfer-files=True" in text
    assert "submit=condor" in text


def test_find_event_config_prefers_submit_ini(tmp_path):
    project = tmp_path / "project"
    event_dir = project / "working" / "S1"
    event_dir.mkdir(parents=True)
    (event_dir / "config.ini").write_text("label=source\n")
    submit_ini = event_dir / "config.target.ini"
    submit_ini.write_text("label=target\n")
    (event_dir / "status.yaml").write_text(yaml.safe_dump({"submit_ini": str(submit_ini)}))

    assert find_event_config(project, "S1") == submit_ini


def test_find_event_config_ignores_generated_configs(tmp_path):
    project = tmp_path / "project"
    event_dir = project / "working" / "S1"
    event_dir.mkdir(parents=True)
    original = event_dir / "config.ini"
    original.write_text("label=source\n")
    (event_dir / "config.target.ini").write_text("label=target\n")
    (event_dir / "config.gwave.ini").write_text("label=gwave\n")

    assert find_event_config(project, "S1") == original


def test_host_profiles_loads_arbitrary_names(tmp_path):
    path = tmp_path / "hosts.yaml"
    path.write_text(yaml.safe_dump({"hosts": {"alpha": {"ssh": "user@alpha", "home": "/home/user", "project_dir": "/home/user/project", "hostname_contains": ["alpha"]}}}))
    profiles = HostProfiles.load(path)
    assert profiles["alpha"].ssh == "user@alpha"
    assert profiles["alpha"].home == Path("/home/user")
