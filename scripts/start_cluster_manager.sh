#!/usr/bin/env bash
set -euo pipefail

# Start/stop/status the Purohit tunnel/static manager for one cluster-local
# project. All paths and tunables come from scripts/init_env.sh unless already
# set in the environment.
#
# This script intentionally launches the manager directly with the current
# process environment. Do not use `bash -lc` here: login shells can reset PATH
# and drop the active conda/IGWN environment, causing tools such as bilby_pipe
# to disappear from the manager health checks.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/init_env.sh"

ACTION="${1:-start}"
PYTHON_BIN="${PYTHON:-python}"

mkdir -p "${PROJECT_DIR}/control" "${WEBDIR}"

resolve_executable() {
    local exe="$1"
    if command -v "$exe" >/dev/null 2>&1; then
        command -v "$exe"
    else
        echo "$exe"
    fi
}

running_python() {
    local pid="$1"
    if [[ -r "/proc/${pid}/exe" ]]; then
        readlink -f "/proc/${pid}/exe" 2>/dev/null || true
    fi
}

ensure_token() {
    if [[ ! -s "${TOKEN_FILE}" ]]; then
        "${PYTHON_BIN}" - <<PY
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

remove_stale_pidfile() {
    if [[ -f "${PIDFILE}" ]] && ! is_running; then
        echo "[purohit-manager] removing stale pidfile=${PIDFILE}"
        rm -f "${PIDFILE}"
    fi
}

print_running_status() {
    local pid
    pid="$(cat "${PIDFILE}")"
    echo "[purohit-manager] running pid=${pid}"
    echo "[purohit-manager] url=http://${HOST}:${PORT}/tunnel.html"
    echo "[purohit-manager] project_dir=${PROJECT_DIR}"
    echo "[purohit-manager] webdir=${WEBDIR}"
    echo "[purohit-manager] token_file=${TOKEN_FILE}"
    echo "[purohit-manager] log=${LOGFILE}"
    local actual_python
    actual_python="$(running_python "${pid}")"
    if [[ -n "${actual_python}" ]]; then
        echo "[purohit-manager] running_python=${actual_python}"
    fi
}

case "${ACTION}" in
    start)
        remove_stale_pidfile
        ensure_token
        if is_running; then
            print_running_status
            desired_python="$(resolve_executable "${PYTHON_BIN}")"
            actual_python="$(running_python "$(cat "${PIDFILE}")")"
            if [[ -n "${actual_python}" && "${actual_python}" != "$(readlink -f "${desired_python}" 2>/dev/null || echo "${desired_python}")" ]]; then
                echo "[purohit-manager] WARNING: manager is already running under a different Python."
                echo "[purohit-manager] desired_python=${desired_python}"
                echo "[purohit-manager] run: ${BASH_SOURCE[0]} restart"
            fi
            exit 0
        fi
        echo "[purohit-manager] starting tunnel/static manager"
        echo "[purohit-manager] project_dir=${PROJECT_DIR}"
        echo "[purohit-manager] webdir=${WEBDIR}"
        echo "[purohit-manager] token_file=${TOKEN_FILE}"
        echo "[purohit-manager] log=${LOGFILE}"
        echo "[purohit-manager] python=$(resolve_executable "${PYTHON_BIN}")"
        echo "[purohit-manager] bilby_pipe=$(command -v bilby_pipe || true)"
        nohup "${PYTHON_BIN}" "${PUROHIT_REPO}/scripts/run_tunnel_manager.py" \
            --project-dir "${PROJECT_DIR}" \
            --webdir "${WEBDIR}" \
            --host "${HOST}" \
            --port "${PORT}" \
            --token-file "${TOKEN_FILE}" \
            --interval "${INTERVAL}" \
            --plot-interval "${PLOT_INTERVAL}" \
            --env-mode "${ENV_MODE}" \
            >"${LOGFILE}" 2>&1 &
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
            remove_stale_pidfile
            echo "[purohit-manager] not running"
        fi
        ;;
    status)
        if is_running; then
            print_running_status
        else
            remove_stale_pidfile
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
