#!/usr/bin/env bash
# Open a laptop-side SSH tunnel to a running Purohit tunnel manager.
#
# Typical use from your laptop:
#   scripts/open_laptop_tunnel.sh --host gwave
#
# Then open:
#   http://127.0.0.1:8766/login.html
#
# The remote Purohit manager must already be running on the submit host, e.g.
#   python scripts/run_tunnel_manager.py --host 127.0.0.1 --port 8766 ...

set -euo pipefail

ssh_host="${PUROHIT_TUNNEL_HOST:-gwave}"
local_bind="${PUROHIT_TUNNEL_LOCAL_BIND:-127.0.0.1}"
local_port="${PUROHIT_TUNNEL_LOCAL_PORT:-8766}"
remote_bind="${PUROHIT_TUNNEL_REMOTE_BIND:-127.0.0.1}"
remote_port="${PUROHIT_TUNNEL_REMOTE_PORT:-8766}"
background=0
no_mux=1
verbose=0

usage() {
    cat <<'EOF'
Usage:
  scripts/open_laptop_tunnel.sh [options]

Options:
  --host HOST              SSH host or alias for the submit host. Default: $PUROHIT_TUNNEL_HOST or gwave
  --local-port PORT        Local laptop port to open. Default: $PUROHIT_TUNNEL_LOCAL_PORT or 8766
  --remote-port PORT       Remote Purohit manager port. Default: $PUROHIT_TUNNEL_REMOTE_PORT or 8766
  --local-bind ADDR        Local bind address. Default: 127.0.0.1
  --remote-bind ADDR       Remote bind address. Default: 127.0.0.1
  --background             Run SSH tunnel in background with -fN
  --allow-mux              Do not disable SSH ControlMaster/ControlPath multiplexing
  --verbose                Pass -v to ssh
  -h, --help               Show this help

Examples:
  scripts/open_laptop_tunnel.sh --host gwave
  scripts/open_laptop_tunnel.sh --host vaishak.prasad@gwave.ldas.cit --local-port 8767 --remote-port 8766

After the tunnel is open, browse to:
  http://127.0.0.1:<local-port>/login.html
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --host)
            ssh_host="$2"
            shift 2
            ;;
        --local-port)
            local_port="$2"
            shift 2
            ;;
        --remote-port)
            remote_port="$2"
            shift 2
            ;;
        --local-bind)
            local_bind="$2"
            shift 2
            ;;
        --remote-bind)
            remote_bind="$2"
            shift 2
            ;;
        --background)
            background=1
            shift
            ;;
        --allow-mux)
            no_mux=0
            shift
            ;;
        --verbose)
            verbose=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if ! command -v ssh >/dev/null 2>&1; then
    echo "ssh was not found on PATH" >&2
    exit 127
fi

forward_spec="${local_bind}:${local_port}:${remote_bind}:${remote_port}"
url_host="127.0.0.1"
if [[ "$local_bind" != "127.0.0.1" && "$local_bind" != "localhost" ]]; then
    url_host="$local_bind"
fi

ssh_args=(
    -o ExitOnForwardFailure=yes
    -o ServerAliveInterval=60
    -o ServerAliveCountMax=3
    -L "$forward_spec"
)

# Avoid stale/overlong ControlPath failures by default.  Use --allow-mux if you
# intentionally want your ~/.ssh/config multiplexing settings to apply.
if [[ "$no_mux" -eq 1 ]]; then
    ssh_args+=( -o ControlMaster=no -S none )
fi

if [[ "$verbose" -eq 1 ]]; then
    ssh_args+=( -v )
fi

if [[ "$background" -eq 1 ]]; then
    ssh_args+=( -fN )
else
    ssh_args+=( -N )
fi

cat <<EOF
[purohit tunnel] SSH host:      ${ssh_host}
[purohit tunnel] Forward:       ${forward_spec}
[purohit tunnel] Browser URL:   http://${url_host}:${local_port}/login.html
[purohit tunnel] Multiplexing:  $([[ "$no_mux" -eq 1 ]] && echo disabled || echo allowed)
EOF

exec ssh "${ssh_args[@]}" "$ssh_host"
