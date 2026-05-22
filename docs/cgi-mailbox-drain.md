# CGI mailbox drain workflow

This workflow is for CIT-style deployments where the `ldas-jobs` CGI host can execute CGI scripts and can write to its own local `/tmp` or `/var/tmp`, but cannot write to `/home` because `/home` is mounted read-only on the CGI host. It also assumes `/tmp` and `/var/tmp` on the CGI host are not directly visible from `citlogin5`.

Instead of having CGI write directly into `project_dir/control/commands.json`, the CGI endpoint uses a local mailbox on the CGI host. The static manager running on `citlogin5` periodically drains that mailbox over HTTPS and executes the commands locally.

## Architecture

```text
Browser button
  -> POST to CGI mailbox endpoint on ldas-jobs/jobs5
  -> CGI appends JSONL command to jobs5:/var/tmp/purohit-.../commands.jsonl
  -> manager on citlogin5 periodically POSTs mode=drain to the same CGI URL
  -> CGI returns queued commands and clears the mailbox
  -> manager executes commands on citlogin5
  -> manager republishes public_html/monitor/index.html and status.json
  -> manager also writes public_html/monitor/commands.html for browser controls
```

The CGI endpoint does not run Condor or shell commands. It only appends or drains command records. The manager remains the process that executes job actions.

## Install the CGI mailbox endpoint

Choose a spool directory on the CGI host. Since `/tmp` and `/var/tmp` are local to `jobs5`, this path is intentionally on the CGI host only:

```bash
python scripts/install_cgi_mailbox_ingress.py \
  --spool-dir /var/tmp/purohit-vaishak-rean5 \
  --cgi-path /home/vaishak.prasad/public_html/cgi-bin/purohit_mailbox.cgi \
  --repo-root /home/vaishak.prasad/Projects/Codes/purohit \
  --python-executable python3

chmod 711 /home/vaishak.prasad
chmod 755 /home/vaishak.prasad/public_html /home/vaishak.prasad/public_html/cgi-bin
chmod 755 /home/vaishak.prasad/public_html/cgi-bin/purohit_mailbox.cgi
```

Open the CGI URL. A plain GET may return an error or status JSON depending on the query, but it should execute as CGI rather than showing the script source.

```text
https://ldas-jobs.ligo.caltech.edu/~vaishak.prasad/cgi-bin/purohit_mailbox.cgi?mode=status
```

## Optional token protection

Create a token file under your project directory on `citlogin5`:

```bash
mkdir -p /home/vaishak.prasad/Projects/ligo/rean5/control
python - <<'PY'
import secrets
from pathlib import Path
path = Path('/home/vaishak.prasad/Projects/ligo/rean5/control/cgi_mailbox_token.txt')
path.write_text(secrets.token_urlsafe(32) + '\n')
path.chmod(0o600)
print(path)
PY
```

Install the CGI with the token file path. The CGI host has `/home` mounted read-only, but it can still read the token file if permissions allow traversal and read access for your own UID:

```bash
python scripts/install_cgi_mailbox_ingress.py \
  --spool-dir /var/tmp/purohit-vaishak-rean5 \
  --cgi-path /home/vaishak.prasad/public_html/cgi-bin/purohit_mailbox.cgi \
  --token-file /home/vaishak.prasad/Projects/ligo/rean5/control/cgi_mailbox_token.txt \
  --repo-root /home/vaishak.prasad/Projects/Codes/purohit \
  --python-executable python3
```

The browser control page has a token input. The token is stored only in browser local storage if you click Save.

## Run the mailbox-draining manager

```bash
python scripts/run_static_manager_with_mailbox.py \
  --project-dir /home/vaishak.prasad/Projects/ligo/rean5 \
  --webdir /home/vaishak.prasad/public_html/monitor \
  --mailbox-url https://ldas-jobs.ligo.caltech.edu/~vaishak.prasad/cgi-bin/purohit_mailbox.cgi \
  --interval 60 \
  --plot-interval 300
```

If using a token:

```bash
python scripts/run_static_manager_with_mailbox.py \
  --project-dir /home/vaishak.prasad/Projects/ligo/rean5 \
  --webdir /home/vaishak.prasad/public_html/monitor \
  --mailbox-url https://ldas-jobs.ligo.caltech.edu/~vaishak.prasad/cgi-bin/purohit_mailbox.cgi \
  --token-file /home/vaishak.prasad/Projects/ligo/rean5/control/cgi_mailbox_token.txt \
  --interval 60 \
  --plot-interval 300
```

Then open:

```text
https://ldas-jobs.ligo.caltech.edu/~vaishak.prasad/monitor/index.html
```

For job-control buttons, open:

```text
https://ldas-jobs.ligo.caltech.edu/~vaishak.prasad/monitor/commands.html
```

## Safety scope

Supported queued actions are limited to:

- `submit_event`
- `hold_event`
- `release_event`
- `remove_event`
- `refresh`

No arbitrary shell command execution is supported by the CGI endpoint. The manager records command processing results in the existing project audit log.
