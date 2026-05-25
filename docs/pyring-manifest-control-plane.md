# pyRing manifest jobs through the Purohit control plane

This workflow keeps pyRing and Purohit responsibilities separate.

```text
pyRing
  generates CE-STM events, pyRing configs, and manifest CSV files

Purohit ManifestRerun
  materializes manifest rows into the normal Purohit project layout
  writes job.submit files and status.yaml ledgers
  optionally submits jobs with condor_submit

Purohit static/tunnel manager
  publishes the existing Monitor / Commands / Files / Health UI
  submits manifest jobs via condor_submit
  submits bilby_pipe jobs via bilby_pipe --submit
```

The goal is to avoid a parallel pyRing monitor. A pyRing job should look like a normal Purohit-managed event to the existing monitor and tunnel command manager.

## Prepare and submit a CE-STM pyRing smoke test

Run on the submit host, for example `gwave`:

```bash
cd /scratch2/ligo.org/vaishak.prasad/Projects/Codes/purohit
git checkout ce-stm-pyring-control-plane
chmod +x scripts/*.sh

NJOBS=10 \
RESET_TESTROOT=1 \
SUBMIT=1 \
bash scripts/init_pyring_ce_stm_test_jobs.sh
```

Important environment overrides:

```bash
PYRING=/scratch2/ligo.org/vaishak.prasad/Projects/Codes/pyRing
PUROHIT=/scratch2/ligo.org/vaishak.prasad/Projects/Codes/purohit
TESTROOT=$HOME/ce_stm_tunnel_test
PROJECT_DIR=$TESTROOT/purohit_project
NJOBS=10
SUBMIT=1
```

The initializer writes a pyRing prior policy that fixes only the free-amplitude degeneracies:

```json
["logdistance", "cosiota", "phi"]
```

and leaves sky and `t0` sampled. This relies on the pyRing CE branch's truncated-noise evidence patch for sampled sky/time.

## Start the existing Purohit cluster-local manager

On the submit host:

```bash
cd /scratch2/ligo.org/vaishak.prasad/Projects/Codes/purohit

PROJECT_DIR=$HOME/ce_stm_tunnel_test/purohit_project \
WEBDIR=$HOME/ce_stm_tunnel_test/purohit_project/web \
PORT=8766 \
bash scripts/start_cluster_manager.sh start
```

This starts the normal Purohit tunnel/static manager. It does not start a pyRing-specific mini monitor.

Useful commands:

```bash
bash scripts/start_cluster_manager.sh status
bash scripts/start_cluster_manager.sh stop
bash scripts/start_cluster_manager.sh restart
```

## Open the UI from a laptop

From the laptop, using an SSH config alias such as `gwave`:

```bash
cd /path/to/purohit

SSH_HOST=gwave \
LOCAL_PORT=8766 \
REMOTE_PORT=8766 \
SHOW_TOKEN=1 \
bash scripts/start_laptop_tunnel.sh
```

Then open:

```text
http://127.0.0.1:8766/index.html
http://127.0.0.1:8766/tunnel.html
http://127.0.0.1:8766/files.html
http://127.0.0.1:8766/health.html
```

Paste the printed token into the existing Purohit tunnel UI before sending commands.

## Existing bilby PE behavior

The existing bilby PE submit path is preserved. The manager only routes to `condor_submit <job.submit>` when an event status is marked as a manifest workflow or when a `submit_file` is recorded. Otherwise it continues to stage inputs, if configured, and runs:

```bash
bilby_pipe <config> --submit
```

## Status metadata

Manifest-prepared events include status metadata such as:

```yaml
workflow_type: manifest
application: pyring
manifest: /path/to/manifest_purohit.csv
command_template: pyRing --config-file {config}
submit_file: /path/to/job.submit
submit_ini: /path/to/config.ini
output: /path/to/pyring/output
```

These fields let the existing static monitor and tunnel manager display and operate on pyRing jobs without a separate control-plane implementation.

## Central command center

For multi-cluster use, run the normal cluster-local manager on each target cluster and configure the existing central command center to read each cluster's webdir/status snapshot. The central command center should route commands to the relevant cluster's queue; the cluster-local manager still performs the actual Condor operation.
