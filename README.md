# purohit

Utilities for preparing, submitting, and monitoring `bilby_pipe` reruns.

Purohit is designed for a shared gravitational-wave rerun project where one or
more submit hosts prepare event working directories, submit HTCondor DAGs through
`bilby_pipe`, and publish a lightweight browser control panel.

## What Purohit does

Purohit supports three related workflows:

1. **Same-cluster operation**: prepare, submit, and monitor jobs on the same host
   where the input INIs already live.
2. **Remote import to a submit cluster**: discover INIs on a source host, copy
   only selected event INIs and their referenced input files to a target submit
   cluster, rewrite paths, and submit locally from the target cluster.
3. **Central command center**: aggregate multiple cluster-local Purohit managers
   into one browser-facing control panel.

The important safety rule is that submission and Condor operations stay local to
the relevant submit host. The central manager routes commands; it does not run
`bilby_pipe`, `condor_q`, or `condor_rm` itself.

## Repository layout

```text
reanalyze/
  reanalyze.py              legacy PERerun preparation/submission helpers
  static_monitor.py         static status/event-page publisher
  tunnel_manager.py         localhost API, command queue, file browser backend
  tunnel_webapp.py          web app entrypoint with public static pages + tokened API
  output_products.py        event-scoped product/config discovery and serving
  remote_import.py          explicit source-host -> target-cluster materialization
  central_manager.py        multi-cluster status aggregator and command router
scripts/
  run_tunnel_manager.py     recommended cluster-local manager entrypoint
  import_remote_events.py   remote source -> target project import CLI
  run_central_manager.py    central multi-cluster manager entrypoint
docs/
  remote-event-import.md
  central-command-center.md
```

## Installation

### Clone and editable install

On the submit host:

```bash
git clone https://github.com/vaishakp/purohit.git
cd purohit
python -m pip install -e .
```

This preserves the existing package layout. The import path remains:

```python
from reanalyze.reanalyze import PERerun
```

### Conda environment

Create a conda environment with the runtime dependencies available on conda-forge:

```bash
conda env create -f conda-environment.yml
conda activate purohit
python -m pip install -e .
```

Some deployment-specific dependencies, such as HTCondor and `bilby_pipe`, are
expected to be installed in the target LIGO/HTCondor environment when needed.
Before submitting real jobs, check:

```bash
which bilby_pipe
which condor_q
which condor_submit
python -c "import yaml, pandas, numpy; print('python deps ok')"
```

## Project directory layout

A Purohit project directory usually has this structure:

```text
/path/to/project/
  working/
    EVENT_NAME/
      config.ini              copied or materialized submit config
      status.yaml             event status, jobid, submit_ini, etc.
      pe/                     bilby_pipe output directory after submission
  control/
    tunnel_token.txt          browser/API token for cluster-local manager
    tunnel_commands.jsonl     command queue consumed by tunnel manager
    processed/                archived command files/results
  submitted_jobs.txt          ledger of submitted events
```

For remote-imported events, the event directory may also contain:

```text
working/EVENT_NAME/
  original/
    config.source.ini
  data/
    home-relative/
      ... copied source-HOME-relative input files ...
  config.target.ini
  input_manifest.json
```

`status.yaml` may record `submit_ini`. When present, the submission manager uses
that explicit target config rather than guessing from the first `*.ini` file.

## One-time token setup

The browser UI uses a token for commands, health checks, file browsing, and event
product/config access. Create a token file readable only by the submit account:

```bash
mkdir -p /path/to/project/control
python - <<'PY'
import secrets
from pathlib import Path
path = Path('/path/to/project/control/tunnel_token.txt')
path.write_text(secrets.token_urlsafe(32) + '\n')
path.chmod(0o600)
print(path)
PY
```

Use the same path with `--token-file` when starting the tunnel manager.

## Workflow A: run and submit on the same cluster

Use this when the input event INIs are already accessible on the submit host.

### 1. Prepare or copy event INIs

The expected state before web submission is:

```text
/path/to/project/working/EVENT_NAME/*.ini
```

or a `status.yaml` containing an explicit `submit_ini`.

For example:

```bash
mkdir -p /path/to/project/working/S240413p
cp /path/to/source/S240413p/config.ini /path/to/project/working/S240413p/config.ini
```

Check that `bilby_pipe` can see the config from the submit host:

```bash
cd /path/to/project/working/S240413p
ls -lh *.ini
bilby_pipe --help >/dev/null
```

### 2. Start the cluster-local tunnel manager

Run this on the submit/login host where Condor commands should execute:

```bash
cd /path/to/purohit
python scripts/run_tunnel_manager.py \
  --project-dir /path/to/project \
  --webdir /path/to/public_html/monitor \
  --host 127.0.0.1 \
  --port 8766 \
  --token-file /path/to/project/control/tunnel_token.txt \
  --interval 10 \
  --plot-interval 300 \
  --env-mode redacted
```

This manager:

```text
1. publishes static pages into --webdir;
2. serves a localhost API at 127.0.0.1:8766;
3. drains control/tunnel_commands.jsonl;
4. runs bilby_pipe <event-config> --submit locally;
5. queries condor_q / condor_history locally;
6. publishes status.json, dag_details.json, health.json, command_results.json.
```

### 3. Open the browser UI

If the webdir is served by the cluster web server, open the corresponding web
URL. For an LVK LDAS-style public_html deployment this is typically similar to:

```text
https://<cluster-web-host>/~<username>/monitor/
```

Then open `login.html`, paste the token from:

```bash
cat /path/to/project/control/tunnel_token.txt
```

and use `index.html`, `tunnel.html`, `files.html`, and the event detail pages.

### 4. SSH tunnel for commands/products

The static pages are loaded from the webdir, but command submission and live
product/config access go through the local API. From your laptop:

```bash
ssh -N -L 8766:127.0.0.1:8766 <submit-login-host>
```

Keep this tunnel open while using the command buttons or event product/config
previews. The default browser endpoint is:

```text
http://127.0.0.1:8766
```

## Workflow B: import from a source host and submit on another cluster

Use this when the source config tree is large and should not be copied wholesale
to the target submit cluster. The expensive discovery runs on the source host;
only selected event INIs and referenced input files are copied.

### 1. Create host profiles

On the target project, create `control/hosts.yaml`:

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

The names `source` and `target` are arbitrary. Do not hard-code usernames or
cluster names in code; put them in this YAML file.

### 2. Import selected events

Run from the target cluster, or from a host that can SSH/rsync from the source:

```bash
cd /path/to/purohit
python scripts/import_remote_events.py \
  --hosts /home/submituser/Projects/ligo/rean5/control/hosts.yaml \
  --source-host source \
  --target-host target \
  --source-dir /home/source_user/path/to/large/source/tree \
  --target-project-dir /home/submituser/Projects/ligo/rean5 \
  --apx NRSur7dq4 \
  --event S240413p
```

This writes a target-local config and manifest under:

```text
/home/submituser/Projects/ligo/rean5/working/S240413p/
```

Verify:

```bash
cat /home/submituser/Projects/ligo/rean5/working/S240413p/status.yaml
ls -lh /home/submituser/Projects/ligo/rean5/working/S240413p/
```

Then start the cluster-local tunnel manager on the target cluster using the same
command as Workflow A. Submission remains local to the target manager.

More details are in `docs/remote-event-import.md`.

## Workflow C: central command center for multiple clusters

Use this when multiple cluster-local managers are running and you want one
browser control panel.

Each cluster still needs its own local manager from Workflow A. Then create a
central config, for example `control/central.yaml`:

```yaml
clusters:
  cluster_a:
    ssh: user@cluster-a-login.example.org
    project_dir: /home/user/Projects/ligo/rean5
    webdir: /home/user/public_html/monitor
    label: Cluster A

  cluster_b:
    ssh: user@cluster-b-login.example.org
    project_dir: /home/user/Projects/ligo/rean5
    webdir: /home/user/public_html/monitor
    label: Cluster B
```

Run the central manager on your laptop or a trusted login host:

```bash
cd /path/to/purohit
python scripts/run_central_manager.py \
  --config /path/to/control/central.yaml \
  --webdir /path/to/central-webdir \
  --host 127.0.0.1 \
  --port 8770 \
  --token-file /path/to/central_token.txt \
  --interval 30
```

Open `central.html` from the central webdir and set the browser endpoint to:

```text
http://127.0.0.1:8770
```

The central manager reads each cluster's `status.json` and routes commands to
that cluster's `control/tunnel_commands.jsonl`. It does not submit jobs itself.

More details are in `docs/central-command-center.md`.

## Event detail pages

Each event page is written under:

```text
<webdir>/events/<EVENT_NAME>/index.html
```

The event page shows:

```text
1. event status and DAG cluster id;
2. event-scoped configuration INIs;
3. live output plots/logs/products;
4. Condor DAG child jobs;
5. raw detail JSON.
```

Configuration INIs are served only from event-scoped roots. Bilby configs are
recognized from keys such as `submit_ini`, `submitted_config`, and
`staged_config`; pyRing configs are recognized from keys/names containing
`pyring` or `ringdown`.

## Collaborative assignments

If `control/assignments.yaml` exists and is enabled, mutating commands are
restricted by event assignment. If the file is missing or assignments are
disabled, behavior is permissive.

Example:

```yaml
enabled: true
policy: manual
admins:
  - vaishak
manual:
  S240413p: alice
  S240422a: bob
```

The operator is resolved from command metadata first, then environment/user
fallbacks. In a shared submit account, include an explicit operator in command
JSON or use the central UI operator field.

See `docs/collaborative-assignments.md` if present in your checkout.

## Common operations

### Submit one event from the web UI

1. Start `scripts/run_tunnel_manager.py` on the submit host.
2. Open `login.html` and save the token.
3. Open `tunnel.html`.
4. Click `Submit` for the event.
5. Watch `index.html` or the event detail page.

### Submit one event manually through the queue

```bash
cat >> /path/to/project/control/tunnel_commands.jsonl <<'EOF'
{"action": "submit_event", "event": "S240413p", "operator": "alice"}
EOF
```

The tunnel manager will drain the queue on its next cycle.

### Reset an event for resubmission

```bash
cat >> /path/to/project/control/tunnel_commands.jsonl <<'EOF'
{"action": "reset_event", "event": "S240413p", "operator": "alice"}
EOF
```

Reset removes standard job output state such as `pe/` and DAG rescue/lock files,
removes the event from `submitted_jobs.txt`, and sets the event status back to
`pending`.

### Inspect logs and products

Open the event detail page:

```text
<webdir>/events/S240413p/index.html
```

or use the file browser:

```text
<webdir>/files.html
```

The file browser is read-only and constrained to manager-configured roots.

## Troubleshooting

### The page loads but command buttons fail

Check that the SSH tunnel is running:

```bash
ssh -N -L 8766:127.0.0.1:8766 <submit-login-host>
```

Check the manager is alive:

```bash
curl -H "X-Purohit-Token: $(cat /path/to/project/control/tunnel_token.txt)" \
  http://127.0.0.1:8766/api/health
```

### Token rejected

Make sure the token in the browser matches:

```bash
cat /path/to/project/control/tunnel_token.txt
```

Then reopen `login.html` and save the token again.

### Event does not appear

Check that the event directory exists:

```bash
ls -lh /path/to/project/working/EVENT_NAME
```

and that either an INI exists or `status.yaml` records a valid `submit_ini`:

```bash
cat /path/to/project/working/EVENT_NAME/status.yaml
```

### Submission fails immediately

Run the same command manually on the submit host:

```bash
bilby_pipe /path/to/project/working/EVENT_NAME/config.ini --submit
```

or, if `status.yaml` records `submit_ini`, run that path instead.

Check command results:

```bash
cat /path/to/public_html/monitor/command_results.json
cat /path/to/project/control/audit.jsonl
```

### Condor status is empty

Check from the same host/account running the manager:

```bash
condor_q
condor_history -limit 5
```

If these commands fail manually, Purohit cannot monitor jobs from that host.

## Testing and continuous integration

GitHub Actions runs the automated test suite on every pull request and push. The
workflow runs on `ubuntu-latest` with Python 3.10, 3.11, and 3.12. For each
Python version, CI checks out the repository, installs the packages listed in
`requirements-test.txt`, sets `PYTHONPATH=.`, and runs:

```bash
pytest -q
```

The tests do not require a live HTCondor installation or the full target LIGO
runtime environment. Test fixtures provide minimal stubs for optional runtime-only
imports such as `htcondor2` and `waveformtools`; tests that need Condor status
behavior monkeypatch it directly.

To run the same tests locally from the repository root:

```bash
python -m pip install -r requirements-test.txt
PYTHONPATH=. pytest -q
```

## Acknowledgements

The packaging work in this repository builds on the packaging effort proposed by
@chungyinleo in #8, while preserving the existing `reanalyze/` source layout.
