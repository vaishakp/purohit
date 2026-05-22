# Tunnel manager workflow

This workflow avoids the `ldas-jobs` CGI drain/SSO problem by running a small token-protected command and file API on the submit/login host and accessing it through an SSH local-forward.

## Architecture

```text
Browser
  -> static public_html monitor pages
  -> http://127.0.0.1:8766/api/command
  -> SSH tunnel
  -> tunnel manager on citlogin5
  -> local command queue in project_dir/control/tunnel_commands.jsonl
  -> shared process_command executor
  -> audit/status/health publishing into public_html/monitor
```

The command vocabulary and executor are shared with the static managers:

- `submit_event`
- `hold_event`
- `release_event`
- `remove_event`
- `refresh`

If the `reset_event` PR is merged, the same tunnel transport can carry it too because it passes through the shared command executor.

## Start the tunnel manager on citlogin5

```bash
cd /home/vaishak.prasad/Projects/Codes/purohit

python scripts/run_tunnel_manager.py \
  --project-dir /home/vaishak.prasad/Projects/ligo/rean5 \
  --webdir /home/vaishak.prasad/public_html/monitor \
  --host 127.0.0.1 \
  --port 8766 \
  --token-file /home/vaishak.prasad/Projects/ligo/rean5/control/cgi_mailbox_token.txt \
  --interval 10 \
  --plot-interval 300 \
  --env-mode redacted
```

## Open the SSH tunnel from your laptop

```bash
ssh -N -L 8766:127.0.0.1:8766 citlogin5
```

Then open:

```text
https://ldas-jobs.ligo.caltech.edu/~vaishak.prasad/monitor/tunnel.html
```

The default endpoint is:

```text
http://127.0.0.1:8766
```

Paste the token into the page and save it in the browser.

## File browser

Open:

```text
https://ldas-jobs.ligo.caltech.edu/~vaishak.prasad/monitor/files.html
```

The file browser is read-only and constrained to configured roots. By default:

- `project`: `--project-dir`
- `webdir`: `--webdir`

Add additional roots with:

```bash
--file-root label=/path/to/root
```

For example:

```bash
--file-root home=/home/vaishak.prasad \
--file-root event=/home/vaishak.prasad/Projects/ligo/rean5/working/S240413p
```

Do not expose broad roots unless you are comfortable browsing them through your local tunnel. The API still requires the Purohit token.

## Pages

The tunnel manager publishes:

```text
index.html              # static monitor
status.json             # static monitor data
tunnel.html             # tunnel command controls
files.html              # read-only file browser
health.html             # manager health diagnostics
command_results.json    # recent command results
```

The original CGI mailbox workflow can remain available separately. The tunnel workflow does not depend on CGI or CILogon cookies for command execution.
