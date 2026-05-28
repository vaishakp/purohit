#!/usr/bin/env bash
# Open a laptop-side SSH tunnel to a running Purohit tunnel manager.
#
# Typical use from your laptop:
#   scripts/open_laptop_tunnel.sh gwave
#   scripts/open_laptop_tunnel.sh cit
#
# Then open:
#   http://127.0.0.1:8766/login.html
#
# The remote Purohit manager must already be running on the submit host, e.g.
#   python scripts/run_tunnel_manager.py --host 127.0.0.1 --port 8766 ...

set -euo pipefail

target="${PUROHIT_TUNNEL_TARGET:-gwave}"
ssh_host="${PUROHIT_TUNNEL_HOST:-}"
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
  scripts/open_laptop_tunnel.sh [TARGET] [options]

Targets:
  gwave                    Use $PUROHIT_GWAVE_HOST or SSH alias "gwave". Default target.
  cit                      Use $PUROHIT_CIT_HOST or "citlogin5.ligo.caltech.edu".
  USER@HOST | HOST         Use the given SSH host directly.

Options:
  --target TARGET          Same as positional TARGET.
  --host HOST              Explicit SSH host. Overrides TARGET resolution.
  --local-port PORT        Local laptop port to open. Default: $PUROHIT_TUNNEL_LOCAL_PORT or 8766
  --remote-port PORT       Remote Purohit manager port. Default: $PUROHIT_TUNNEL_REMOTE_PORT or 8766
  --local-bind ADDR        Local bind address. Default: 127.0.0.1
  --remote-bind ADDR       Remote bind address. Default: 127.0.0.1
  --background             Run SSH tunnel in background with -fN
  --allow-mux              Do not disable SSH ControlMaster/ControlPath multiplexing
  --verbose                Pass -v to ssh
  -h, --help               Show this help

Examples:
  scripts/open_laptop_tunnel.sh gwave
  scripts/open_laptop_tunnel.sh cit
  PUROHIT_CIT_HOST=vaishak.prasad@citlogin5.ligo.caltech.edu scripts/open_laptop_tunnel.sh cit
  scripts/open_laptop_tunnel.sh vaishak.prasad@citlogin5.ligo.caltech.edu
  scripts/open_laptop_tunnel.sh gwave --local-port 8767 --remote-port 8766

After the tunnel is open, browse to:
  http://127.0.0.1:<local-port>/login.html
EOF
}

resolve_target() {
    case "$1" in
        gwave)
            printf '%s\n' "${PUROHIT_GWAVE_HOST:-gwave}"
            ;;
        cit)
            printf '%s\n' "${PUROHIT_CIT_HOST:-citlogin5.ligo.caltech.edu}"
            ;;
        *)
            printf '%s\n' "$1"
            ;;
    esac
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --target)
            target="$2"
            shift 2
            ;;
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
        --*)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
        *)
            target="$1"
            shift
            ;;
    esac
done

if [[ -z "$ssh_host" ]]; then
    ssh_host="$(resolve_target "$target")"
fi

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
[purohit tunnel] Target:       ${target}
[purohit tunnel] SSH host:     ${ssh_host}
[purohit tunnel] Forward:      ${forward_spec}
[purohit tunnel] Browser URL:  http://${url_host}:${local_port}/login.html
[purohit tunnel] Multiplexing: $([[ "$no_mux" -eq 1 ]] && echo disabled || echo allowed)
EOF

exec ssh "${ssh_args[@]}" "$ssh_host"
