# CGI command ingress for the static manager

CIT `ldas-jobs` can execute user CGI scripts under `~/public_html/cgi-bin`. The CGI command ingress lets the static monitor page send job commands to a CGI script, which appends them to the manager command queue. The background static manager then executes those queued commands.

## Architecture

```text
Browser button
  -> CGI command endpoint
  -> CGI validates optional token
  -> CGI appends to project_dir/control/commands.json
  -> CGI-aware manager polls commands.json
  -> manager runs bilby_pipe / Condor control command
  -> manager republishes monitor/index.html and status.json
```

The CGI endpoint does not execute Condor commands directly. It only writes queue entries. The manager remains the only process that executes job actions.

## Install the CGI script

```bash
mkdir -p ~/public_html/cgi-bin

python scripts/install_cgi_command_ingress.py \
  --command-file /home/vaishak.prasad/Projects/ligo/rean5/control/commands.json \
  --cgi-path /home/vaishak.prasad/public_html/cgi-bin/purohit_command.cgi \
  --python-executable python3

chmod 711 ~
chmod 755 ~/public_html ~/public_html/cgi-bin ~/public_html/cgi-bin/purohit_command.cgi
```

Open the CGI URL to confirm it is reachable. A request without a valid command may return a JSON error, which is acceptable:

```text
https://ldas-jobs.ligo.caltech.edu/~vaishak.prasad/cgi-bin/purohit_command.cgi
```

## Optional token protection

Create a token file readable only by you:

```bash
mkdir -p /home/vaishak.prasad/Projects/ligo/rean5/control
python - <<'PY'
import secrets
from pathlib import Path
path = Path('/home/vaishak.prasad/Projects/ligo/rean5/control/cgi_token.txt')
path.write_text(secrets.token_urlsafe(32) + '\n')
path.chmod(0o600)
print(path)
PY
```

Install the CGI with the token file:

```bash
python scripts/install_cgi_command_ingress.py \
  --command-file /home/vaishak.prasad/Projects/ligo/rean5/control/commands.json \
  --cgi-path /home/vaishak.prasad/public_html/cgi-bin/purohit_command.cgi \
  --token-file /home/vaishak.prasad/Projects/ligo/rean5/control/cgi_token.txt \
  --python-executable python3
```

The static page will show a token input when a command URL is configured. The token is stored only in browser local storage if you click Save.

## Run the CGI-aware manager

```bash
python scripts/run_static_manager_with_cgi.py \
  --project-dir /home/vaishak.prasad/Projects/ligo/rean5 \
  --webdir /home/vaishak.prasad/public_html/monitor \
  --command-url /~vaishak.prasad/cgi-bin/purohit_command.cgi \
  --interval 60 \
  --plot-interval 300
```

Then open:

```text
https://ldas-jobs.ligo.caltech.edu/~vaishak.prasad/monitor/index.html
```

The table will show Submit, Hold, Release, and Remove buttons when `command_url` is present in `status.json`.

## Safety scope

The CGI endpoint accepts only these actions:

- `submit_event`
- `hold_event`
- `release_event`
- `remove_event`
- `refresh`

It does not execute arbitrary shell commands. Keep `commands.json` and any token file writable/readable only by the intended account.
