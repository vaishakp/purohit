# Remote web monitor

Purohit includes a read-only Flask web monitor for remotely inspecting rerun jobs and basic machine resources.

The monitor is intended to run under your user account on the submit/login machine that can see your `project_dir` and query HTCondor with `condor_q`.

## Install monitor dependencies

```bash
python -m pip install -r monitor-requirements.txt
```

If the package is installed with the packaging PR, install the package itself in editable mode as usual:

```bash
python -m pip install -e .
```

## Recommended access pattern: SSH tunnel

On the remote submit/login machine:

```bash
python scripts/run_web_monitor.py --project-dir /path/to/project_dir --host 127.0.0.1 --port 8765
```

From your laptop:

```bash
ssh -L 8765:127.0.0.1:8765 user@submit-host
```

Then open:

```text
http://127.0.0.1:8765
```

## Direct remote binding with token

If you intentionally want to expose the monitor on a network interface, use a token:

```bash
export PUROHIT_MONITOR_TOKEN='choose-a-long-random-token'
python scripts/run_web_monitor.py --project-dir /path/to/project_dir --host 0.0.0.0 --port 8765
```

Then access:

```text
http://submit-host:8765/?token=choose-a-long-random-token
```

Do not expose this to the public internet without institutional approval, firewall rules, and HTTPS/reverse-proxy protection.

## Data shown

The web UI shows:

- event name;
- status from the local ledger/status files and best-effort `condor_q` lookup;
- Condor cluster id;
- requested CPUs;
- requested memory;
- remote host/node when available from Condor;
- remote wall-clock runtime when available;
- local monitor host information: CPU counts, CPU frequency if `psutil` can read it, memory use, load average, and approximate load percent.

## JSON endpoints

- `/health`
- `/api/system`
- `/api/jobs`
- `/api/summary`

If a token is configured, pass it as either `?token=...` or the `X-Auth-Token` header.

## Scope and limitations

This monitor is read-only. It does not submit, hold, release, remove, or modify jobs.

Resource information is best effort. Some node-level CPU and memory details are only visible while Condor still has the job in the queue and exposes attributes such as `RemoteHost`, `RequestCpus`, and `RequestMemory`. Completed jobs may only show local ledger information unless accounting/history support is added later.
