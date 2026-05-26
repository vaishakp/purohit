from __future__ import annotations

from pathlib import Path

import yaml

from reanalyze.output_products import discover_event_configs


def test_discovers_submit_and_pyring_ini_from_status(tmp_path):
    project = tmp_path / "project"
    event_dir = project / "working" / "S1"
    event_dir.mkdir(parents=True)
    bilby = event_dir / "config.target.ini"
    bilby.write_text("label = bilby\n")
    pyring = event_dir / "pyring.ini"
    pyring.write_text("[input]\n")
    (event_dir / "status.yaml").write_text(yaml.safe_dump({"submit_ini": str(bilby), "pyring_ini": str(pyring)}))

    configs = discover_event_configs(event_dir, "S1")

    labels = [item["label"] for item in configs]
    assert any("Bilby INI" in label and "config.target.ini" in label for label in labels)
    assert any("pyRing INI" in label and "pyring.ini" in label for label in labels)
    assert all(item["api_href"].startswith("api/event-product?") for item in configs)


def test_discovers_top_level_generic_ini(tmp_path):
    event_dir = tmp_path / "project" / "working" / "S1"
    event_dir.mkdir(parents=True)
    generic = event_dir / "complete.ini"
    generic.write_text("label = complete\n")

    configs = discover_event_configs(event_dir, "S1")

    assert len(configs) == 1
    assert configs[0]["path"] == "complete.ini"
    assert configs[0]["kind"] == "Config INI"


def test_ignores_status_config_outside_event_roots(tmp_path):
    project = tmp_path / "project"
    event_dir = project / "working" / "S1"
    event_dir.mkdir(parents=True)
    outside = project / "other" / "secret.ini"
    outside.parent.mkdir(parents=True)
    outside.write_text("secret = true\n")
    (event_dir / "status.yaml").write_text(yaml.safe_dump({"bilby_ini": str(outside)}))

    configs = discover_event_configs(event_dir, "S1")

    assert configs == []


def test_discovers_output_root_config_from_status(tmp_path):
    event_dir = tmp_path / "project" / "working" / "S1"
    output_dir = tmp_path / "event-output"
    event_dir.mkdir(parents=True)
    output_dir.mkdir()
    ringdown = output_dir / "pyring_config.ini"
    ringdown.write_text("[pyring]\n")
    (event_dir / "status.yaml").write_text(yaml.safe_dump({"outdir": str(output_dir)}))

    configs = discover_event_configs(event_dir, "S1")

    assert any(item["kind"] == "pyRing INI" and item["source"] == "outdir" for item in configs)
