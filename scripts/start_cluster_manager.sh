#!/usr/bin/env bash
set -euo pipefail

# Start the existing Purohit tunnel/static manager for one cluster-local project.
# This is intentionally workflow-agnostic: it serves the same UI for bilby_pipe
# projects and manifest/pyRing projects, as long as jobs live under the normal
# Purohit project layout.

export PROJECT_DIR="${PROJECT_DIR:-$HOME/ce_stm_tunnel_test/purohit_project}"
export WEBDIR="${WEBDIR:-$PROJECT_DIR/web}"
export HOST="${HOST:-127.0.0.1}"
export PORT="${PORT:-8766}"
export INTERVAL="${INTERVAL:-30}"
export PLOT_INTERVAL="${PLOT_INTERVAL:-120}"
export TOKEN_FILE="${TOKEN_FILE:-$PROJECT_DIR/control/tunnel_token.txt}"
export PIDFILE="${PIDFILE:-$PROJECT_DIR/control/tunnel_manager.pid}"
export LOGFILE="${LOGFILE:-$PROJECT_DIR/control/tunnel_manager.log}"
export ACTION="${1:-start}"

mkdir -p "$PROJECT_DIR/control" "$WEBDIR"

ensure_token() {
    if [[ ! -s "$TOKEN_FILE" ]]; then
        python - <<PY
from pathlib import Path
import secrets
path = Path("$TOKEN_FILE")
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(secrets.token_urlsafe(32) + "\n")
path.chmod(0o600)
print(path)
PY
    fi
}

is_running() {
    [[ -f "$PIDFILE" ]] || return 1
    local pid
    pid=$(cat "$PIDFILE")
    [[ -n "$pid" ]] || return 1
    kill -0 "$pid" 2>/dev/null
}

case "$ACTION" in
    start)
        ensure_token
        if is_running; then
            echo "[purohit-manager] already running pid=$(cat "$PIDFILE")"
            echo "[purohit-manager] url=http://$HOST:$PORT/tunnel.html"
            echo "[purohit-manager] token_file=$TOKEN_FILE"
            exit 0
        fi
        echo "[purohit-manager] starting existing tunnel/static manager"
        nohup python scripts/run_tunnel_manager.py \
            --project-dir "$PROJECT_DIR" \
            --webdir "$WEBDIR" \
            --host "$HOST" \
            --port "$PORT" \
            --token-file "$TOKEN_FILE" \
            --interval "$INTERVAL" \
            --plot-interval "$PLOT_INTERVAL" \
            >"$LOGFILE" 2>&1 &
        echo $! > "$PIDFILE"
        sleep 1
        if is_running; then
            echo "[purohit-manager] started pid=$(cat "$PIDFILE")"
            echo "[purohit-manager] url=http://$HOST:$PORT/tunnel.html"
            echo "[purohit-manager] webdir=$WEBDIR"
            echo "[purohit-manager] token_file=$TOKEN_FILE"
            echo "[purohit-manager] log=$LOGFILE"
        else
            echo "[purohit-manager] failed to start; log follows" >&2
            cat "$LOGFILE" 2>/dev/null || true
            exit 1
        fi
        ;;
    stop)
        if is_running; then
            echo "[purohit-manager] stopping pid=$(cat "$PIDFILE")"
            kill "$(cat "$PIDFILE")" || true
            rm -f "$PIDFILE"
        else
            echo "[purohit-manager] not running"
        fi
        ;;
    status)
        if is_running; then
            echo "[purohit-manager] running pid=$(cat "$PIDFILE")"
            echo "[purohit-manager] url=http://$HOST:$PORT/tunnel.html"
            echo "[purohit-manager] token_file=$TOKEN_FILE"
        else
            echo "[purohit-manager] stopped"
            exit 1
        fi
        ;;
    restart)
        "$0" stop || true
        "$0" start
        ;;
    *)
        echo "usage: $0 {start|stop|status|restart}" >&2
        exit 2
        ;;
esac
