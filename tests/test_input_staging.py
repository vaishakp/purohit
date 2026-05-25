from __future__ import annotations

from pathlib import Path

import yaml

from reanalyze.input_staging import stage_bilby_inputs


def test_staging_disabled_returns_original_config(tmp_path):
    project = tmp_path / "project"
    event_dir = project / "working" / "S1"
    event_dir.mkdir(parents=True)
    config = event_dir / "config.ini"
    config.write_text("psd_file = /tmp/nope.dat\n")

    staged = stage_bilby_inputs(project, "S1", config)

    assert staged.enabled is False
    assert staged.config_path == config


def test_stages_and_rewrites_existing_input_file(tmp_path):
    project = tmp_path / "project"
    control = project / "control"
    control.mkdir(parents=True)
    source_dir = project / "inputs"
    source_dir.mkdir()
    psd = source_dir / "H1_psd.dat"
    psd.write_text("1 2\n")
    event_dir = project / "working" / "S1"
    event_dir.mkdir(parents=True)
    config = event_dir / "config.ini"
    config.write_text(f"psd_file = {psd}\n")
    (control / "staging.yaml").write_text(yaml.safe_dump({"enabled": True, "copy_roots": [str(project)]}))

    staged = stage_bilby_inputs(project, "S1", config)

    assert staged.enabled is True
    assert staged.config_path.name == "config.staged.ini"
    assert staged.manifest_path is not None
    rewritten = staged.config_path.read_text()
    assert str(psd) not in rewritten
    assert "staged_inputs/H1_psd.dat" in rewritten
    assert (event_dir / "staged_inputs" / "H1_psd.dat").read_text() == "1 2\n"


def test_preserves_cvmfs_path(tmp_path):
    project = tmp_path / "project"
    control = project / "control"
    control.mkdir(parents=True)
    event_dir = project / "working" / "S1"
    event_dir.mkdir(parents=True)
    config = event_dir / "config.ini"
    config.write_text("basis_file = /cvmfs/example/basis.hdf5\n")
    (control / "staging.yaml").write_text(yaml.safe_dump({"enabled": True}))

    staged = stage_bilby_inputs(project, "S1", config)

    assert staged.enabled is True
    assert "/cvmfs/example/basis.hdf5" in staged.config_path.read_text()
    assert staged.copied_files == ()
