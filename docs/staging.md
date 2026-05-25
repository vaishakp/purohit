# Staging bilby input files for another submit account

Purohit can rewrite an event's copied bilby INI so that local input files are available from another account or filesystem namespace, for example a shared `gwave` account at CIT.

The staging layer is disabled by default. Enable it with:

```yaml
# project_dir/control/staging.yaml
enabled: true
mode: rsync

target_host: gwave@citlogin5.ligo.caltech.edu
remote_project_dir: /home/gwave/Projects/ligo/rean5
submit_mode: remote

remote_preamble: "source /home/gwave/soft/anaconda3/etc/profile.d/conda.sh && conda activate gw5"

stage_subdir: staged_inputs
rewrite_config_suffix: .gwave.ini

copy_roots:
  - /home/vaishak.prasad/Projects/ligo/rean5
  - /home/vaishak.prasad/Projects/ligo/shared_inputs

preserve_roots:
  - /cvmfs
  - /archive
  - /frames

rsync_args:
  - -a
  - --partial
  - --protect-args
```

When `submit_event` runs, Purohit now does:

```text
find working/<event>/*.ini
scan file-like config keys for existing local files
copy those files into the staged event directory
write a rewritten config with staged paths
write input_staging_manifest.json
run bilby_pipe on the rewritten config
```

For `mode: rsync`, files are copied to:

```text
<remote_project_dir>/working/<event>/staged_inputs/
```

and the rewritten config is copied to:

```text
<remote_project_dir>/working/<event>/<original-stem>.gwave.ini
```

If `submit_mode: remote`, the manager submits by running the submit command on `target_host` from the remote event directory.

## Local/shared-filesystem mode

For a shared filesystem or group-writable directory, use:

```yaml
enabled: true
mode: local
target_project_dir: /home/gwave/Projects/ligo/rean5
submit_mode: local
copy_roots:
  - /home/vaishak.prasad/Projects/ligo/rean5
```

This copies inputs to the local target project tree and runs local `bilby_pipe` on the rewritten config.

## What gets copied

Purohit only stages existing regular files whose config keys look file-like, for example keys containing:

```text
psd, calibration, calib, data, file, prior, injection, lookup, roq, basis,
weights, spline, envelope
```

It rewrites simple path values and Python-literal dictionaries/lists, such as:

```ini
psd-dict = {'H1': '/path/H1_psd.dat', 'L1': '/path/L1_psd.dat'}
calibration-envelope-dict = {'H1': '/path/H1_cal.txt'}
```

The following are intentionally skipped unless explicitly included:

```text
outdir, webdir, label, accounting, scheduler, detectors, duration, trigger_time
```

Paths under `preserve_roots` are not copied or rewritten.

## Manifest

Each staging attempt writes:

```text
working/<event>/input_staging_manifest.json
```

The manifest records the source config, rewritten config, copied files, skipped paths, target host, submit mode, and exact submit command. This is the primary place to debug staging behavior.

## Failure modes

If the manager can see a path in the INI but the submit account cannot, use `mode: rsync`. If the manager cannot read a path at all, staging cannot copy it; move the file under a shared group-readable root or run the manager from an account that can read it.

Set `strict_missing: true` to fail submission when a file-like absolute path is missing locally. By default, missing paths are recorded in the manifest as skipped and submission continues.
