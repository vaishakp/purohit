from __future__ import annotations

import json
from pathlib import Path

import yaml

from reanalyze.central_manager import (
    ClusterConfig,
    aggregate_snapshots,
    load_clusters,
    remote_append_jsonl,
)


def test_load_clusters_with_arbitrary_names(tmp_path):
    path = tmp_path / "central.yaml"
    path.write_text(yaml.safe_dump({"clusters": {"alpha": {"project_dir": "/tmp/project", "webdir": "/tmp/web", "label": "Alpha"}}}))
    clusters = load_clusters(path)
    assert len(clusters) == 1
    assert clusters[0].name == "alpha"
    assert clusters[0].queue_path == Path("/tmp/project/control/tunnel_commands.jsonl")


def test_aggregate_snapshots_tags_jobs_by_cluster():
    clusters = [ClusterConfig(name="alpha", project_dir=Path("/p"), webdir=Path("/w"))]
    snapshots = [{"ok": True, "status": {"generated_at": 1.0, "jobs": [{"event": "S1", "status": "pending"}]}}]
    payload = aggregate_snapshots(clusters, snapshots)
    assert payload["clusters"][0]["name"] == "alpha"
    assert payload["clusters"][0]["job_count"] == 1
    assert payload["jobs"][0]["cluster"] == "alpha"
    assert payload["jobs"][0]["cluster_event_id"] == "alpha:S1"


def test_remote_append_jsonl_local(tmp_path):
    cluster = ClusterConfig(name="local", project_dir=tmp_path / "project", webdir=tmp_path / "web")
    queue = cluster.project_dir / "control" / "tunnel_commands.jsonl"
    payload = {"action": "submit_event", "event": "S1"}
    remote_append_jsonl(cluster, queue, payload)
    lines = queue.read_text().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == payload
