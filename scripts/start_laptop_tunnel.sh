#!/usr/bin/env bash
set -euo pipefail

# Laptop-side SSH tunnel launcher for the existing Purohit tunnel/static UI.
# Uses an SSH config alias by default, e.g.
#
#   Host gwave
#       HostName ligo01.gwave.ics.psu.edu
#       User vaishak.prasad
#       GSSAPIAuthentication yes
#       GSSAPIDelegateCredentials yes

export SSH_HOST="${SSH_HOST:-gwave}"
export LOCAL_PORT="${LOCAL_PORT:-8766}"
export REMOTE_PORT="${REMOTE_PORT:-8766}"
export KEEP_TUNNEL="${KEEP_TUNNEL:-0}"
export START_REMOTE_MANAGER="${START_REMOTE_MANAGER:-1}"
export AUTO_GIT_PULL="${AUTO_GIT_PULL:-1}"
export SHOW_TOKEN="${SHOW_TOKEN:-0}"

export REMOTE_PUROHIT="${REMOTE_PUROHIT:-/scratch2/ligo.org/vaishak.prasad/Projects/Codes/purohit}"
export REMOTE_PROJECT_DIR="${REMOTE_PROJECT_DIR:-\$HOME/ce_stm_tunnel_test/purohit_project}"
export REMOTE_WEBDIR="${REMOTE_WEBDIR:-\$HOME/ce_stm_tunnel_test/purohit_project/web}"
export REMOTE_TOKEN_FILE="${REMOTE_TOKEN_FILE:-\$HOME/ce_stm_tunnel_test/purohit_project/control/tunnel_token.txt}"

SAFE_HOST_NAME=$(echo "$SSH_HOST" | tr -c 'A-Za-z0-9_.-' '_')
CONTROL_SOCKET="${CONTROL_SOCKET:-${TMPDIR:-/tmp}/purohit_${SAFE_HOST_NAME}_${LOCAL_PORT}.sock}"

cleanup() {
    echo
    if [[ "$KEEP_TUNNEL" == "1" ]]; then
        echo "[laptop] KEEP_TUNNEL=1; leaving SSH control connection open: $CONTROL_SOCKET"
    else
        echo "[laptop] closing SSH control connection"
        ssh -S "$CONTROL_SOCKET" -O exit "$SSH_HOST" 2>/dev/null || true
    fi
}
trap cleanup EXIT

if ssh -S "$CONTROL_SOCKET" -O check "$SSH_HOST" 2>/dev/null; then
    echo "[laptop] closing stale SSH control connection at $CONTROL_SOCKET"
    ssh -S "$CONTROL_SOCKET" -O exit "$SSH_HOST" 2>/dev/null || true
fi

echo "[laptop] starting tunnel with alias $SSH_HOST"
echo "[laptop] forwarding http://127.0.0.1:$LOCAL_PORT -> $SSH_HOST:127.0.0.1:$REMOTE_PORT"
ssh -fN -M -S "$CONTROL_SOCKET" \
    -L "${LOCAL_PORT}:127.0.0.1:${REMOTE_PORT}" \
    "$SSH_HOST"

echo "[laptop] testing remote connection"
ssh -S "$CONTROL_SOCKET" "$SSH_HOST" 'echo "[remote] host=$(hostname) user=$(whoami)"; condor_q -version | head -1 || true'

if [[ "$START_REMOTE_MANAGER" == "1" ]]; then
    echo "[laptop] starting existing Purohit cluster-local manager on $SSH_HOST"
    ssh -S "$CONTROL_SOCKET" "$SSH_HOST" \
        "set -euo pipefail; cd $REMOTE_PUROHIT; if [[ '$AUTO_GIT_PULL' == '1' && -d .git ]]; then git pull --ff-only; fi; PROJECT_DIR=$REMOTE_PROJECT_DIR WEBDIR=$REMOTE_WEBDIR PORT=$REMOTE_PORT TOKEN_FILE=$REMOTE_TOKEN_FILE bash scripts/start_cluster_manager.sh start"
else
    echo "[laptop] START_REMOTE_MANAGER=0; not starting remote manager"
fi

echo
echo "[laptop] Existing Purohit UI is available at:"
echo "  Monitor : http://127.0.0.1:$LOCAL_PORT/index.html"
echo "  Commands: http://127.0.0.1:$LOCAL_PORT/tunnel.html"
echo "  Files   : http://127.0.0.1:$LOCAL_PORT/files.html"
echo "  Health  : http://127.0.0.1:$LOCAL_PORT/health.html"
echo
if [[ "$SHOW_TOKEN" == "1" ]]; then
    echo "[laptop] Remote tunnel token:"
    ssh -S "$CONTROL_SOCKET" "$SSH_HOST" "cat $REMOTE_TOKEN_FILE" || true
else
    echo "[laptop] Token file on remote host: $REMOTE_TOKEN_FILE"
    echo "[laptop] To print it once: SHOW_TOKEN=1 $0"
fi
echo
echo "[laptop] Press Ctrl-C to close the tunnel."

while true; do
    sleep 3600
done
