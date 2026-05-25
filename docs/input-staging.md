# Bilby input staging

Purohit can stage local files referenced by copied bilby `.ini` files before submitting a job. This is useful when the config contains paths to PSDs, calibration-envelope files, data dumps, prior files, injection files, ROQ assets, or lookup tables that are readable on one CIT account but not on the submit account.

The feature is disabled by default. Enable it by creating:

```text
<project_dir>/control/staging.yaml
```

## Local/shared-filesystem mode

Use this when the submit account can see the same filesystem path after files are copied into a shared project tree.

```yaml
enabled: true

# Default mode. Copies files locally and rewrites the config to local staged paths.
mode: local

# Optional. Defaults to the current project dir.
local_project_dir: /home/vaishak.prasad/Projects/ligo/rean5

# Defaults to working/<event>.
event_subdir: working/{event}

stage_subdir: staged_inputs
rewrite_config_suffix: .staged.ini

# Only paths below these roots will be copied. Leave empty to allow any readable file.
copy_roots:
  - /home/vaishak.prasad/Projects/ligo/rean5
  - /home/vaishak.prasad/Projects/ligo/shared_inputs

# These are treated as already globally available and are not copied.
preserve_roots:
  - /cvmfs
  - /archive
  - /frames
  - /hdfs

# Raise if a path-looking value under a selected key does not exist.
strict_missing: false

# Record sha256 checksums in the manifest.
hash_files: true
```

When `submit_event` is run, Purohit writes:

```text
working/<event>/staged_inputs/<copied files>
working/<event>/<original-name>.staged.ini
working/<event>/input_manifest.json
```

and submits the staged config.

## Remote account mode: first implementation

This PR intentionally implements the conservative first stage of remote support: copy files into a local staging directory and optionally rewrite paths to a remote prefix. You can then sync the staged directory to the submit account using your preferred transport, e.g. `rsync`, `scp`, or a shared group copy.

Example:

```yaml
enabled: true
mode: local
stage_subdir: staged_inputs
rewrite_config_suffix: .gwave.ini

# If set, paths inside the rewritten config point here instead of the local staging dir.
remote_stage_prefix: /home/gwave/Projects/ligo/rean5/working/{event}/staged_inputs
```

Then sync manually or from a wrapper:

```bash
rsync -a --partial \
  /home/vaishak.prasad/Projects/ligo/rean5/working/S240413p/staged_inputs/ \
  gwave@citlogin5.ligo.caltech.edu:/home/gwave/Projects/ligo/rean5/working/S240413p/staged_inputs/

rsync -a --partial \
  /home/vaishak.prasad/Projects/ligo/rean5/working/S240413p/*gwave.ini \
  gwave@citlogin5.ligo.caltech.edu:/home/gwave/Projects/ligo/rean5/working/S240413p/
```

A later PR can add a fully automatic rsync backend once the exact gwave SSH route, account policy, and target layout are stable.

## What gets detected

Purohit scans INI keys whose names contain one of:

```text
file, path, psd, calibration, envelope, data, dump, prior, injection, roq, basis, weights, lookup
```

Only values that look path-like and resolve to existing files are copied. Dictionary/list values are parsed when possible.

## Manifest

`input_manifest.json` records:

```json
{
  "event": "S240413p",
  "source_config": "...complete.ini",
  "rewritten_config": "...complete.staged.ini",
  "files": [
    {
      "section": "DEFAULT",
      "key": "psd_dict",
      "source": "/original/H1_psd.dat",
      "staged": "/staged/H1_psd.dat",
      "size_bytes": 12345,
      "sha256": "..."
    }
  ]
}
```

The event `status.yaml` is also updated with `staged_config`, `input_manifest`, and `staged_input_count` after successful submission.

## Safety notes

This is intentionally conservative:

- missing files are ignored unless `strict_missing: true`;
- global roots like `/cvmfs` are preserved by default;
- no network copy is attempted by this first PR;
- the original config is never modified in place.
