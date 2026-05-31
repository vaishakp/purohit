#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/init_env.sh"

args=(
  "${PUROHIT_REPO}/scripts/init_project.py"
  --hosts "${HOSTS_FILE}"
  --source-host "${SOURCE_HOST}"
  --source-dir "${SOURCE_DIR}"
  --project-dir "${PROJECT_DIR}"
  --apx "${APX}"
  --mode "${INIT_MODE}"
  --token-file "${TOKEN_FILE}"
)

if [[ -n "${TARGET_HOST}" ]]; then
  args+=(--target-host "${TARGET_HOST}")
fi

if [[ -f "${APPROVALS_FILE}" ]]; then
  args+=(--approvals-yaml "${APPROVALS_FILE}")
else
  echo "[purohit init] approvals file not found; continuing without it: ${APPROVALS_FILE}" >&2
fi

if [[ -n "${EVENTS}" ]]; then
  for event in ${EVENTS}; do
    args+=(--event "${event}")
  done
fi

cat <<EOF
[purohit init] repo=${PUROHIT_REPO}
[purohit init] source_dir=${SOURCE_DIR}
[purohit init] project_dir=${PROJECT_DIR}
[purohit init] webdir=${WEBDIR}
[purohit init] token_file=${TOKEN_FILE}
[purohit init] apx=${APX}
[purohit init] mode=${INIT_MODE}
EOF

python "${args[@]}"
