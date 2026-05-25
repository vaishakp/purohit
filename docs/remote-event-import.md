# Remote event import

This workflow is for cases where the source bilby configuration tree is too large to copy to a submit cluster. The expensive discovery step runs on the source host, while only selected event INIs and their referenced input files are copied to the target project.

The existing same-host workflow is unchanged. If source and target are the same cluster, keep using the existing `PERerun` and manager workflow. Remote import is an explicit separate stage.

## Host profiles

Create a host profile file, for example `control/hosts.yaml`:

```yaml
hosts:
  source:
    ssh: user@source-login.example.org
    home: /home/source_user
    hostname_contains:
      - source-login
    scheduler: condor

  target:
    ssh: submituser@target-login.example.org
    home: /home/submituser
    project_dir: /home/submituser/Projects/ligo/rean5
    scheduler: condor
```

The profile names are arbitrary. The code uses source and target host profiles and does not hard-code cluster or user names.

## Import from source host to target project

Run this from the target cluster or from a host that can access the source host:

```bash
python scripts/import_remote_events.py \
  --hosts /home/submituser/Projects/ligo/rean5/control/hosts.yaml \
  --source-host source \
  --target-host target \
  --source-dir /home/source_user/path/to/large/source \
  --target-project-dir /home/submituser/Projects/ligo/rean5 \
  --apx NRSur7dq4 \
  --event S240413p
```

Internally this does:

```text
1. run config discovery under source_dir on the source host
2. select matching event INIs
3. copy the selected INI only
4. scan the copied INI for path-valued dependencies
5. copy those dependency files only
6. preserve source HOME-relative paths under working/<event>/data/home-relative/
7. write a target submit INI
8. write input_manifest.json and status.yaml
```

## Target layout

For event `S240413p`, a target project gets:

```text
working/S240413p/
  original/
    config.source.ini
  data/
    home-relative/
      Projects/...
  config.target.ini
  input_manifest.json
  status.yaml
```

`status.yaml` records `submit_ini`. The submission manager now prefers `submit_ini` when present, so the tunnel/static manager submits the target-cluster config instead of accidentally selecting a generated or source config.

## Path policy

Dependency paths found in path-like keys are copied to event-local data paths:

```text
/source/home/Projects/shared/psds/H1.dat
  -> <target_project>/working/<event>/data/home-relative/Projects/shared/psds/H1.dat
```

Remaining source-home paths in the INI are rewritten by preserving the path suffix after `HOME`:

```text
/source/home/Projects/ligo/rean5/...
  -> /target/home/Projects/ligo/rean5/...
```

Preserved roots such as `/cvmfs`, `/archive`, `/frames`, and `/hdfs` are not copied by default.

## Submission

After import, run the tunnel/static manager on the target cluster. Submission remains local to the manager process:

```bash
python scripts/run_tunnel_manager.py \
  --project-dir /home/submituser/Projects/ligo/rean5 \
  --webdir /home/submituser/public_html/monitor \
  --host 127.0.0.1 \
  --port 8766
```

This means jobs imported to the target cluster are submitted from the target cluster, while same-cluster jobs can still use the existing workflow.
