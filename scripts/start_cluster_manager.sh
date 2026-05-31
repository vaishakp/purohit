#!/usr/bin/env bash
set -euo pipefail

# Start/stop/status the Purohit tunnel/static manager for one cluster-local
# project. All paths and tunables come from scripts/init_env.sh unless already
# set in the environment.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/init_env.sh"

ACTION="${1:-start}"

mkdir -p "${PROJECT_DIR}/control" "${WEBDIR}"

ensure_token() {
    if [[ ! -s "${TOKEN_FILE}" ]]; then
        python - <<PY
from pathlib import Path
import secrets
path = Path("${TOKEN_FILE}")
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(secrets.token_urlsafe(32) + "\n")
path.chmod(0o600)
print(path)
PY
    fi
}

is_running() {
    [[ -f "${PIDFILE}" ]] || return 1
    local pid
    pid="$(cat "${PIDFILE}")"
    [[ -n "${pid}" ]] || return 1
    kill -0 "${pid}" 2>/dev/null
}

manager_command() {
    python "${PUROHIT_REPO}/scripts/run_tunnel_manager.py" \
        --project-dir "${PROJECT_DIR}" \
        --webdir "${WEBDIR}" \
        --host "${HOST}" \
        --port "${PORT}" \
        --token-file "${TOKEN_FILE}" \
        --interval "${INTERVAL}" \
        --plot-interval "${PLOT_INTERVAL}" \
        --env-mode "${ENV_MODE}"
}

case "${ACTION}" in
    start)
        ensure_token
        if is_running; then
            echo "[purohit-manager] already running pid=$(cat "${PIDFILE}")"
            echo "[purohit-manager] url=http://${HOST}:${PORT}/tunnel.html"
            echo "[purohit-manager] project_dir=${PROJECT_DIR}"
            echo "[purohit-manager] webdir=${WEBDIR}"
            echo "[purohit-manager] token_file=${TOKEN_FILE}"
            exit 0
        fi
        echo "[purohit-manager] starting tunnel/static manager"
        echo "[purohit-manager] project_dir=${PROJECT_DIR}"
        echo "[purohit-manager] webdir=${WEBDIR}"
        echo "[purohit-manager] token_file=${TOKEN_FILE}"
        echo "[purohit-manager] log=${LOGFILE}"
        nohup bash -lc "$(declare -f manager_command); manager_command" >"${LOGFILE}" 2>&1 &
        echo $! > "${PIDFILE}"
        sleep 1
        if is_running; then
            echo "[purohit-manager] started pid=$(cat "${PIDFILE}")"
            echo "[purohit-manager] url=http://${HOST}:${PORT}/tunnel.html"
        else
            echo "[purohit-manager] failed to start; log follows" >&2
            cat "${LOGFILE}" 2>/dev/null || true
            exit 1
        fi
        ;;
    stop)
        if is_running; then
            echo "[purohit-manager] stopping pid=$(cat "${PIDFILE}")"
            kill "$(cat "${PIDFILE}")" || true
            rm -f "${PIDFILE}"
        else
            echo "[purohit-manager] not running"
        fi
        ;;
    status)
        if is_running; then
            echo "[purohit-manager] running pid=$(cat "${PIDFILE}")"
            echo "[purohit-manager] url=http://${HOST}:${PORT}/tunnel.html"
            echo "[purohit-manager] project_dir=${PROJECT_DIR}"
            echo "[purohit-manager] webdir=${WEBDIR}"
            echo "[purohit-manager] token_file=${TOKEN_FILE}"
            echo "[purohit-manager] log=${LOGFILE}"
        else
            echo "[purohit-manager] stopped"
            exit 1
        fi
        ;;
    restart)
        "${BASH_SOURCE[0]}" stop || true
        "${BASH_SOURCE[0]}" start
        ;;
    *)
        echo "usage: $0 {start|stop|status|restart}" >&2
        exit 2
        ;;
esac
