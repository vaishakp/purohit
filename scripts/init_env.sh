#!/usr/bin/env bash
# Shared Purohit project environment.
#
# Source this file before running project initialization or the cluster manager:
#   source scripts/init_env.sh
#
# Override any variable before sourcing, e.g.
#   PROJECT_NAME=run2 SOURCE_DIR=/path/to/source/working source scripts/init_env.sh

# Do not use `set -euo pipefail` here: this file is intended to be sourced by
# interactive shells and wrapper scripts.

_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_DEFAULT_REPO="$(cd "${_SCRIPT_DIR}/.." && pwd)"

export PUROHIT_REPO="${PUROHIT_REPO:-${_DEFAULT_REPO}}"
export PROJECT_NAME="${PROJECT_NAME:-run1}"

# Source tree containing event directories as immediate children:
#   $SOURCE_DIR/<event>/*.ini
# For the current GWTC5-HLV layout this is usually:
#   /home/pe.o4/GWTC5-HLV/project/working
export SOURCE_DIR="${SOURCE_DIR:-/home/pe.o4/GWTC5-HLV/project/working}"

# Writable Purohit project/control root. Event dirs are created under:
#   $PROJECT_DIR/working/<event>/
export PROJECT_DIR="${PROJECT_DIR:-${HOME}/Projects/ligo/${PROJECT_NAME}}"
export WEBDIR="${WEBDIR:-${PROJECT_DIR}/web}"
export TOKEN_FILE="${TOKEN_FILE:-${PROJECT_DIR}/control/tunnel_token.txt}"

export HOSTS_FILE="${HOSTS_FILE:-${PUROHIT_REPO}/scripts/hosts.yaml}"
export APPROVALS_FILE="${APPROVALS_FILE:-${PUROHIT_REPO}/approved_runs.json}"
export SOURCE_HOST="${SOURCE_HOST:-cit}"
export TARGET_HOST="${TARGET_HOST:-}"
export INIT_MODE="${INIT_MODE:-auto}"
export APX="${APX:-IMRPhenomXPHM}"

export HOST="${HOST:-127.0.0.1}"
export PORT="${PORT:-8766}"
export INTERVAL="${INTERVAL:-10}"
export PLOT_INTERVAL="${PLOT_INTERVAL:-300}"
export ENV_MODE="${ENV_MODE:-redacted}"
export PIDFILE="${PIDFILE:-${PROJECT_DIR}/control/tunnel_manager.pid}"
export LOGFILE="${LOGFILE:-${PROJECT_DIR}/control/tunnel_manager.log}"

# Optional repeatable EVENT list. Use a whitespace-separated list, e.g.
#   export EVENTS="S240414s S240413p"
export EVENTS="${EVENTS:-}"

unset _SCRIPT_DIR _DEFAULT_REPO
