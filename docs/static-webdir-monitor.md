# Static webdir job monitor

This monitor is designed for LIGO/CIT-style environments where the submit host is reachable through `sshproxy` and two-factor authentication, making long-lived SSH tunnels inconvenient or unreliable.

Instead of exposing a live Flask app, the monitor runs under your user account on the submit/login side and periodically writes static files into an existing web-accessible directory, such as a bilby/PESummary `webdir`.

## Architecture

```text
CIT / submit side:
  publish_web_monitor.py
    -> reads project_dir/submitted_jobs.txt
    -> reads project_dir/working/<event>/status.yaml
    -> queries condor_q / optionally condor_history
    -> writes webdir/monitor/index.html and status.json

Remote browser:
  opens the existing authenticated webdir URL
  reads index.html and status.json only
```

No inbound connection to the submit host is required after the publisher is running.

## One-shot publishing

```bash
python scripts/publish_web_monitor.py \
  --project-dir /path/to/project_dir \
  --webdir /path/to/webdir/monitor \
  --once
```

This writes:

```text
/path/to/webdir/monitor/index.html
/path/to/webdir/monitor/status.json
```

## Periodic publishing

```bash
python scripts/publish_web_monitor.py \
  --project-dir /path/to/project_dir \
  --webdir /path/to/webdir/monitor \
  --interval 300
```

The command will refresh the static files every five minutes.

## Running through cron

Example crontab entry for a five-minute refresh:

```cron
*/5 * * * * cd /path/to/purohit && /path/to/python scripts/publish_web_monitor.py --project-dir /path/to/project_dir --webdir /path/to/webdir/monitor --once >> /path/to/project_dir/monitor.log 2>&1
```

## Running through a user systemd timer

If user-level systemd timers are allowed on the host, create a service like:

```ini
[Unit]
Description=Purohit static webdir monitor publisher

[Service]
Type=oneshot
WorkingDirectory=/path/to/purohit
ExecStart=/path/to/python scripts/publish_web_monitor.py --project-dir /path/to/project_dir --webdir /path/to/webdir/monitor --once
```

and a timer like:

```ini
[Unit]
Description=Refresh Purohit static monitor

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min

[Install]
WantedBy=timers.target
```

## Viewing remotely

Open the URL corresponding to your existing authenticated webdir, for example:

```text
https://<existing-webdir-host>/<path-to-webdir>/monitor/index.html
```

The exact host/path depends on the LIGO/CIT webdir mapping used by your bilby/PESummary outputs.

## Data shown

The monitor reports:

- event name;
- local ledger/status from `submitted_jobs.txt` and `status.yaml`;
- best-effort `condor_q -json` status;
- optional `condor_history -json` status for jobs no longer in the live queue;
- Condor cluster id;
- requested CPUs;
- requested memory;
- remote host/node when available from Condor;
- remote wall-clock runtime when available;
- disk/RSS-like fields when exposed by Condor;
- publisher-host CPU count, CPU frequency, memory use, and load average;
- optional per-event heartbeat information from `project_dir/working/<event>/heartbeat.json`.

## Optional heartbeat files

For richer execute-node information, a job wrapper may write a small JSON file to:

```text
project_dir/working/<event>/heartbeat.json
```

Example content:

```json
{
  "hostname": "slot-node.example",
  "load_avg_1m": 1.2,
  "cpu_count": 64,
  "memory_available_gb": 120.4,
  "timestamp": 1760000000
}
```

The publisher will include this heartbeat in `status.json` and display it in the web UI.

## Scope and limitations

This monitor is read-only. It does not submit, hold, release, remove, or otherwise modify jobs.

Resource reporting is best effort. Detailed execute-node CPU speed, load, and memory information may not be available through Condor alone. For that, use heartbeat files or future job-wrapper support.
