#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/init_env.sh"

mkdir -p "${PROJECT_DIR}/control" "${WEBDIR}"

PYTHON_BIN="${PYTHON:-python}"

echo "[purohit-manager] project_dir=${PROJECT_DIR}"
echo "[purohit-manager] webdir=${WEBDIR}"
echo "[purohit-manager] token_file=${TOKEN_FILE}"
echo "[purohit-manager] python=$(command -v "${PYTHON_BIN}" || echo "${PYTHON_BIN}")"
echo "[purohit-manager] bilby_pipe=$(command -v bilby_pipe || true)"

"${PYTHON_BIN}" "${PUROHIT_REPO}/scripts/run_tunnel_manager.py" \
  --project-dir "${PROJECT_DIR}" \
  --webdir "${WEBDIR}" \
  --host "${HOST}" \
  --port "${PORT}" \
  --token-file "${TOKEN_FILE}" \
  --interval "${INTERVAL}" \
  --plot-interval "${PLOT_INTERVAL}" \
  --env-mode "${ENV_MODE}"
