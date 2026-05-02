#!/usr/bin/env bash
set -euo pipefail

# Local self-bootstrap installer for Andyria.
# - Installs/updates Python environment
# - Builds Rust crates
# - Optionally installs a persistent systemd user service
# - Runs extension hooks from deploy/hooks.d/*.sh

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PATH="${VENV_PATH:-${REPO_ROOT}/python/.venv}"
CONFIG_PATH="${CONFIG_PATH:-${REPO_ROOT}/deploy/server/config.yaml}"
ENABLE_SERVICE="${ENABLE_SERVICE:-1}"
SERVICE_NAME="andyria-local.service"

log() { printf '[andyria-self] %s\n' "$*"; }
warn() { printf '[andyria-self] warning: %s\n' "$*" >&2; }

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    printf '[andyria-self] error: missing command: %s\n' "$1" >&2
    exit 1
  fi
}

require_cmd python3
require_cmd cargo

log "Creating/updating virtual environment at ${VENV_PATH}"
python3 -m venv "${VENV_PATH}"
# shellcheck source=/dev/null
source "${VENV_PATH}/bin/activate"
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e "${REPO_ROOT}/python[dev,llm]"

log "Building Rust workspace"
(
  cd "${REPO_ROOT}/rust"
  cargo build --release
)

if [[ "${ENABLE_SERVICE}" == "1" ]] && command -v systemctl >/dev/null 2>&1; then
  UNIT_DIR="${HOME}/.config/systemd/user"
  mkdir -p "${UNIT_DIR}"
  cat > "${UNIT_DIR}/${SERVICE_NAME}" <<UNIT
[Unit]
Description=Andyria Local Node
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${REPO_ROOT}
ExecStart=${VENV_PATH}/bin/python -m andyria serve --config ${CONFIG_PATH}
Restart=always
RestartSec=3
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
UNIT

  systemctl --user daemon-reload
  systemctl --user enable "${SERVICE_NAME}" >/dev/null
  systemctl --user restart "${SERVICE_NAME}"
  log "Persistent user service installed: ${SERVICE_NAME}"
else
  warn "systemctl user service not configured (ENABLE_SERVICE=${ENABLE_SERVICE})"
fi

if ls "${REPO_ROOT}/deploy/hooks.d"/*.sh >/dev/null 2>&1; then
  log "Running extension hooks"
  for hook in "${REPO_ROOT}/deploy/hooks.d"/*.sh; do
    bash "${hook}"
  done
fi

log "Self-install complete"
