# Static webdir manager

The static manager extends the static webdir monitor with a narrow command queue. It is intended for CIT/LIGO-style environments where the browser can read a `public_html`/PESummary-style directory but cannot directly call a live web server on the submit host.

## Architecture

```text
submit/login host:
  scripts/run_static_manager.py
    -> reads project_dir/control/commands.json
    -> executes supported commands
    -> appends project_dir/control/audit.jsonl
    -> refreshes public_html/monitor/index.html and status.json
    -> periodically copies selected run artifacts into public_html/monitor/artifacts/

browser:
  opens https://.../~user/monitor/index.html
  reads static HTML/JSON only
```

The browser page is static. It does not itself write to the submit host. Commands are queued by writing JSON on the submit side, either manually, through `scripts/write_manager_command.py`, or through a site-specific authenticated wrapper that writes the same JSON file.

## Run the manager

```bash
python scripts/run_static_manager.py \
  --project-dir /home/vaishak.prasad/Projects/ligo/rean5 \
  --webdir /home/vaishak.prasad/public_html/monitor \
  --interval 60 \
  --plot-interval 300
```

This defaults to the command file:

```text
/home/vaishak.prasad/Projects/ligo/rean5/control/commands.json
```

Use `--once` for a single process/publish pass.

## Queue commands

```bash
python scripts/write_manager_command.py \
  submit_event S240413p \
  --command-file /home/vaishak.prasad/Projects/ligo/rean5/control/commands.json
```

Supported actions are:

- `submit_event`
- `hold_event`
- `release_event`
- `remove_event`
- `refresh`

Equivalent JSON:

```json
{
  "commands": [
    {"action": "submit_event", "event": "S240413p"},
    {"action": "hold_event", "event": "S240413p"},
    {"action": "release_event", "event": "S240413p"},
    {"action": "remove_event", "event": "S240413p"}
  ]
}
```

After processing, the manager empties the command file, archives the previous command payload under `project_dir/control/processed/`, and appends command results to `project_dir/control/audit.jsonl`.

## Output and diagnostic files

The manager periodically copies selected event output files into:

```text
webdir/artifacts/<event>/...
```

By default, common plot/output extensions are exposed, including PNG, SVG, PDF, HTML, logs, JSON, HDF5 files, stdout/stderr-style files, and text files. The static page links these artifacts per event.

Tune the copy cadence with:

```bash
--plot-interval 300
```

and the number of linked files with:

```bash
--max-artifacts-per-event 40
```

## Permissions

Generated `index.html`, `status.json`, and copied artifact files are set to web-readable mode `0644`. Generated directories are made traversable with mode `0755` where possible.

## Safety scope

The manager only supports the explicitly listed actions. It does not execute arbitrary shell commands from the command file. Keep the command file writable only by the intended user or a trusted authenticated ingress wrapper.
