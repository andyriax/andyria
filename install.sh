#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Andyria Universal Installer
# Usage:  curl -fsSL https://andyriax.github.io/andyria/install.sh | bash
#     or: bash install.sh [OPTIONS]
#
# Options:
#   --docker        Install via Docker Compose (default if docker is available)
#   --python        Install as local Python service
#   --no-service    Skip systemd/launchd service registration
#   --dir DIR       Install into DIR (default: ~/andyria)
#   --port PORT     HTTP port (default: 7700)
#   --agent PRESET  Auto-seed with preset agent (coder|analyst|researcher|...)
#   --non-interactive / -y  Skip all prompts, use defaults
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO_URL="https://github.com/andyriax/andyria.git"
PAGES_BASE="https://andyriax.github.io/andyria"
DEFAULT_DIR="${HOME}/andyria"
DEFAULT_PORT="7700"

# ── Parse args ───────────────────────────────────────────────────────────────
MODE=""          # docker | python | auto
INSTALL_DIR=""
PORT="${DEFAULT_PORT}"
INSTALL_SERVICE=1
AUTO_AGENT=""
YES=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --docker)          MODE="docker"; shift ;;
    --python)          MODE="python"; shift ;;
    --no-service)      INSTALL_SERVICE=0; shift ;;
    --dir)             INSTALL_DIR="$2"; shift 2 ;;
    --port)            PORT="$2"; shift 2 ;;
    --agent)           AUTO_AGENT="$2"; shift 2 ;;
    --non-interactive|-y) YES=1; shift ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

INSTALL_DIR="${INSTALL_DIR:-${DEFAULT_DIR}}"

# ── Helpers ──────────────────────────────────────────────────────────────────
BOLD=$'\033[1m'
CYAN=$'\033[36m'
GREEN=$'\033[32m'
YELLOW=$'\033[33m'
RED=$'\033[31m'
RESET=$'\033[0m'

log()  { printf "${CYAN}[andyria]${RESET} %s\n" "$*"; }
ok()   { printf "${GREEN}[andyria] ✓${RESET} %s\n" "$*"; }
warn() { printf "${YELLOW}[andyria] ⚠${RESET} %s\n" "$*" >&2; }
die()  { printf "${RED}[andyria] ✗${RESET} %s\n" "$*" >&2; exit 1; }

has() { command -v "$1" >/dev/null 2>&1; }

ask() {
  local prompt="$1" default="$2" answer
  if [[ "${YES}" == "1" ]]; then echo "${default}"; return; fi
  read -rp "${CYAN}[andyria]${RESET} ${prompt} [${default}]: " answer
  echo "${answer:-${default}}"
}

ask_yn() {
  local prompt="$1" default="$2"
  if [[ "${YES}" == "1" ]]; then [[ "${default}" == "y" ]] && return 0 || return 1; fi
  local answer
  read -rp "${CYAN}[andyria]${RESET} ${prompt} (y/n) [${default}]: " answer
  answer="${answer:-${default}}"
  [[ "${answer,,}" == "y" ]]
}

# ── Banner ────────────────────────────────────────────────────────────────────
cat <<'BANNER'
  ___                _              _
 / _ \   _ __    __| | _   _  _ __(_)  __ _
| |_| | | '_ \  / _` || | | || '__| | / _` |
 \__,_| |_| |_||(_| || |_| || |  | || (_| |
        |_|     \__,_| \__, ||_|  |_| \__,_|
                       |___/
  Edge-first autonomous AI agent platform
BANNER
echo ""

# ── Detect environment ────────────────────────────────────────────────────────
OS="$(uname -s)"
ARCH="$(uname -m)"
IS_TERMUX=0; IS_RPI=0; IS_WSL=0

[[ -n "${TERMUX_VERSION:-}" ]] && IS_TERMUX=1
[[ -f /proc/device-tree/model ]] && grep -qi "raspberry" /proc/device-tree/model 2>/dev/null && IS_RPI=1
[[ -f /proc/version ]] && grep -qi "microsoft\|wsl" /proc/version 2>/dev/null && IS_WSL=1

if [[ "${IS_TERMUX}" == "1" ]]; then
  log "Environment: Termux (Android)"
elif [[ "${IS_RPI}" == "1" ]]; then
  log "Environment: Raspberry Pi"
elif [[ "${IS_WSL}" == "1" ]]; then
  log "Environment: WSL (Windows)"
else
  log "Environment: ${OS} / ${ARCH}"
fi

# ── Determine install mode ────────────────────────────────────────────────────
if [[ -z "${MODE}" ]]; then
  if [[ "${IS_TERMUX}" == "1" || "${IS_RPI}" == "1" ]]; then
    MODE="python"
    log "Edge device detected — using Python install mode"
  elif has docker && has docker-compose || (has docker && docker compose version >/dev/null 2>&1); then
    MODE="docker"
    log "Docker detected — using Docker Compose install mode"
  elif has python3; then
    MODE="python"
    log "No Docker found — using Python install mode"
  else
    die "Neither Docker nor Python 3 found. Install one and re-run."
  fi
fi

log "Install mode : ${BOLD}${MODE}${RESET}"
log "Install dir  : ${BOLD}${INSTALL_DIR}${RESET}"
log "Port         : ${BOLD}${PORT}${RESET}"

if [[ "${YES}" != "1" ]]; then
  ask_yn "Continue with these settings?" "y" || { log "Aborted."; exit 0; }
fi

# ── Clone or update repo ──────────────────────────────────────────────────────
if [[ -d "${INSTALL_DIR}/.git" ]]; then
  log "Updating existing install at ${INSTALL_DIR}"
  git -C "${INSTALL_DIR}" pull --ff-only
else
  log "Cloning Andyria into ${INSTALL_DIR}"
  git clone --depth 1 "${REPO_URL}" "${INSTALL_DIR}"
fi

cd "${INSTALL_DIR}"

# ── Install system dependencies ───────────────────────────────────────────────
install_system_deps() {
  if [[ "${IS_TERMUX}" == "1" ]]; then
    log "Installing Termux packages"
    pkg install -y python rust git curl openssl 2>/dev/null || true
  elif [[ "${OS}" == "Linux" ]]; then
    if has apt-get; then
      log "Installing apt packages"
      sudo apt-get install -y --no-install-recommends python3 python3-pip python3-venv curl git build-essential libssl-dev 2>/dev/null || true
    elif has dnf; then
      sudo dnf install -y python3 python3-pip curl git gcc openssl-devel 2>/dev/null || true
    elif has pacman; then
      sudo pacman -Sy --noconfirm python python-pip curl git base-devel 2>/dev/null || true
    fi
  elif [[ "${OS}" == "Darwin" ]]; then
    if has brew; then
      brew install python3 git curl 2>/dev/null || true
    fi
  fi
}

# ── Docker mode ───────────────────────────────────────────────────────────────
install_docker() {
  log "Setting up Docker Compose environment"

  # Write .env if not present
  if [[ ! -f .env ]]; then
    log "Creating .env from .env.example"
    cp .env.example .env
    sed -i "s/^ANDYRIA_PORT=.*/ANDYRIA_PORT=${PORT}/" .env
    if [[ -n "${AUTO_AGENT}" ]]; then
      echo "ANDYRIA_SEED_AGENT=${AUTO_AGENT}" >> .env
    fi
  else
    warn ".env already exists — not overwriting"
  fi

  log "Building and starting containers"
  docker compose up -d --build

  ok "Andyria running at ${GREEN}http://localhost:${PORT}${RESET}"
  ok "API docs at ${GREEN}http://localhost:${PORT}/docs${RESET}"
}

# ── Python mode ───────────────────────────────────────────────────────────────
install_python() {
  install_system_deps

  VENV="${INSTALL_DIR}/python/.venv"
  log "Creating virtual environment at ${VENV}"
  python3 -m venv "${VENV}"
  # shellcheck source=/dev/null
  source "${VENV}/bin/activate"
  pip install --upgrade pip setuptools wheel -q

  # Detect Rust for ledger crate
  if has cargo; then
    log "Building Rust crates"
    (cd rust && cargo build --release 2>/dev/null) || warn "Rust build failed — ledger will use Python fallback"
  else
    warn "Rust/cargo not found — skipping ledger crate build"
  fi

  log "Installing Andyria Python package"
  if has ollama || [[ "${IS_RPI}" == "0" && "${IS_TERMUX}" == "0" ]]; then
    pip install -e "python/[llm]" -q
  else
    pip install -e "python/" -q
  fi

  # Write config
  CONFIG_PATH="${INSTALL_DIR}/deploy/server/config.yaml"
  if grep -q "port:" "${CONFIG_PATH}" 2>/dev/null; then
    sed -i "s/port:.*/port: ${PORT}/" "${CONFIG_PATH}"
  fi

  # Install systemd service (Linux non-Termux)
  if [[ "${INSTALL_SERVICE}" == "1" && "${IS_TERMUX}" != "1" && "${OS}" == "Linux" ]] && has systemctl; then
    UNIT_DIR="${HOME}/.config/systemd/user"
    mkdir -p "${UNIT_DIR}"
    cat > "${UNIT_DIR}/andyria.service" <<UNIT
[Unit]
Description=Andyria Local Node
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${INSTALL_DIR}
ExecStart=${VENV}/bin/python -m andyria serve --config ${CONFIG_PATH}
Restart=always
RestartSec=3
Environment=PYTHONUNBUFFERED=1
Environment=ANDYRIA_PORT=${PORT}

[Install]
WantedBy=default.target
UNIT
    systemctl --user daemon-reload
    systemctl --user enable andyria.service >/dev/null
    systemctl --user restart andyria.service
    ok "systemd user service installed and started"
  elif [[ "${IS_TERMUX}" == "1" ]]; then
    # Termux: write a start script
    mkdir -p "${HOME}/bin"
    cat > "${HOME}/bin/andyria-start" <<TERMUX
#!/data/data/com.termux/files/usr/bin/bash
source "${VENV}/bin/activate"
python -m andyria serve --config "${CONFIG_PATH}"
TERMUX
    chmod +x "${HOME}/bin/andyria-start"
    ok "Start script installed: ${HOME}/bin/andyria-start"
    log "Run: andyria-start"
    return
  else
    # Foreground fallback
    ok "Starting Andyria (foreground — press Ctrl-C to stop)"
    exec "${VENV}/bin/python" -m andyria serve --config "${CONFIG_PATH}"
  fi

  ok "Andyria running at ${GREEN}http://localhost:${PORT}${RESET}"
}

# ── Seed agent via API ────────────────────────────────────────────────────────
seed_agent() {
  local preset_id="$1"
  log "Seeding agent from preset: ${preset_id}"
  # Wait for the API to be ready
  local retries=0
  until curl -sf "http://localhost:${PORT}/health" >/dev/null 2>&1 || [[ $retries -ge 15 ]]; do
    sleep 2; retries=$((retries+1))
  done
  if curl -sf "http://localhost:${PORT}/health" >/dev/null 2>&1; then
    local presets
    presets="$(curl -sf "http://localhost:${PORT}/v1/agents/presets" || echo '[]')"
    local preset
    preset="$(echo "${presets}" | python3 -c "
import json,sys
ps=json.load(sys.stdin)
p=next((x for x in ps if x.get('id')=='${preset_id}'),None)
if p: print(json.dumps({'name':p['name'],'system_prompt':p.get('system_prompt','')}))
" 2>/dev/null || true)"
    if [[ -n "${preset}" ]]; then
      curl -sf -X POST "http://localhost:${PORT}/v1/agents" \
        -H "Content-Type: application/json" -d "${preset}" >/dev/null
      ok "Agent '${preset_id}' created"
    else
      warn "Preset '${preset_id}' not found"
    fi
  else
    warn "API not reachable — skipping agent seed"
  fi
}

# ── Run ───────────────────────────────────────────────────────────────────────
case "${MODE}" in
  docker) install_docker ;;
  python) install_python ;;
  *)      die "Unknown mode: ${MODE}" ;;
esac

if [[ -n "${AUTO_AGENT}" && "${MODE}" == "python" ]]; then
  seed_agent "${AUTO_AGENT}" &
fi

echo ""
echo "${BOLD}${GREEN}Installation complete!${RESET}"
echo ""
echo "  UI     → http://localhost:${PORT}"
echo "  API    → http://localhost:${PORT}/v1"
echo "  Docs   → http://localhost:${PORT}/docs"
echo ""
echo "  Useful commands:"
echo "    make dev          # Hot-reload dev mode + browser IDE"
echo "    make test         # Run test suite"
echo "    python -m andyria --help"
echo ""
echo "  Learn more: ${PAGES_BASE}"
echo ""
