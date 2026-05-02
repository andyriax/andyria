#!/usr/bin/env bash
set -euo pipefail

# Fully automated + smart guided installer for Raspberry Pi deployment.
#
# Usage examples:
#   ./deploy/raspberry-pi/deploy.sh
#   ./deploy/raspberry-pi/deploy.sh --host 192.168.1.50 --user pi
#   ./deploy/raspberry-pi/deploy.sh --host raspberrypi.local --non-interactive

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

PI_HOST=""
PI_USER="${PI_USER:-pi}"
PI_PORT="${PI_PORT:-22}"
REMOTE_DIR=""
CONFIG_PATH=""
MODEL_URL="${MODEL_URL:-https://huggingface.co/TheBloke/TinyLlama-1.1B-Chat-v1.0-GGUF/resolve/main/tinyllama-1.1b-chat-v1.0.Q2_K.gguf}"
MODEL_PATH=""
NON_INTERACTIVE=0
SKIP_MODEL=0

log() { printf '[andyria] %s\n' "$*"; }
warn() { printf '[andyria] warning: %s\n' "$*" >&2; }
die() { printf '[andyria] error: %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<USAGE
Andyria Raspberry Pi deploy (guided installer)

Options:
  --host <host>            Raspberry Pi host (IP, DNS, or .local)
  --user <user>            SSH user (default: pi)
  --port <port>            SSH port (default: 22)
  --remote-dir <path>      Remote app dir (default: /home/<user>/andyria)
  --config-path <path>     Remote config path (default: /home/<user>/.andyria/config.yaml)
  --model-path <path>      Remote model path (default: /home/<user>/.andyria/models/tinyllama-1.1b-chat-v1.0.Q2_K.gguf)
  --model-url <url>        Model download URL
  --skip-model-download    Skip model download if missing
  --non-interactive        Fail instead of prompting for values
  -h, --help               Show help
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) PI_HOST="$2"; shift 2 ;;
    --user) PI_USER="$2"; shift 2 ;;
    --port) PI_PORT="$2"; shift 2 ;;
    --remote-dir) REMOTE_DIR="$2"; shift 2 ;;
    --config-path) CONFIG_PATH="$2"; shift 2 ;;
    --model-path) MODEL_PATH="$2"; shift 2 ;;
    --model-url) MODEL_URL="$2"; shift 2 ;;
    --skip-model-download) SKIP_MODEL=1; shift ;;
    --non-interactive) NON_INTERACTIVE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "Unknown option: $1" ;;
  esac
done

if [[ -z "${PI_HOST}" ]]; then
  PI_HOST="raspberrypi.local"
fi

if [[ -z "${REMOTE_DIR}" ]]; then
  REMOTE_DIR="/home/${PI_USER}/andyria"
fi

if [[ -z "${CONFIG_PATH}" ]]; then
  CONFIG_PATH="/home/${PI_USER}/.andyria/config.yaml"
fi

if [[ -z "${MODEL_PATH}" ]]; then
  MODEL_PATH="/home/${PI_USER}/.andyria/models/tinyllama-1.1b-chat-v1.0.Q2_K.gguf"
fi

if [[ "${NON_INTERACTIVE}" -eq 0 ]]; then
  log "Smart guided installer"
  read -r -p "Pi host [${PI_HOST}]: " ans; PI_HOST="${ans:-$PI_HOST}"
  read -r -p "SSH user [${PI_USER}]: " ans; PI_USER="${ans:-$PI_USER}"
  read -r -p "SSH port [${PI_PORT}]: " ans; PI_PORT="${ans:-$PI_PORT}"
  read -r -p "Remote dir [${REMOTE_DIR}]: " ans; REMOTE_DIR="${ans:-$REMOTE_DIR}"
fi

SSH_TARGET="${PI_USER}@${PI_HOST}"
SSH_OPTS=(-p "$PI_PORT" -o StrictHostKeyChecking=accept-new)

for cmd in ssh rsync curl; do
  command -v "$cmd" >/dev/null 2>&1 || die "Missing required command: $cmd"
done

log "Checking SSH connectivity to ${SSH_TARGET}:${PI_PORT}"
if ! ssh "${SSH_OPTS[@]}" -o ConnectTimeout=8 "$SSH_TARGET" 'echo ok' >/dev/null 2>&1; then
  if [[ "${NON_INTERACTIVE}" -eq 1 ]]; then
    die "SSH connection failed to ${SSH_TARGET}:${PI_PORT}"
  fi
  warn "SSH connectivity check failed."
  read -r -p "Continue anyway and let ssh prompt for auth? [y/N]: " go
  [[ "${go,,}" == "y" ]] || die "Aborted by user"
fi

log "Syncing repository to ${SSH_TARGET}:${REMOTE_DIR}"
rsync -az --delete \
  --exclude ".git" \
  --exclude "**/__pycache__" \
  --exclude "**/.pytest_cache" \
  --exclude "rust/target" \
  --exclude "python/.venv" \
  -e "ssh -p ${PI_PORT} -o StrictHostKeyChecking=accept-new" \
  "${REPO_ROOT}/" "${SSH_TARGET}:${REMOTE_DIR}/"

log "Bootstrapping runtime and service on Raspberry Pi"
ssh "${SSH_OPTS[@]}" "$SSH_TARGET" \
  PI_USER="$PI_USER" \
  REMOTE_DIR="$REMOTE_DIR" \
  CONFIG_PATH="$CONFIG_PATH" \
  MODEL_PATH="$MODEL_PATH" \
  MODEL_URL="$MODEL_URL" \
  SKIP_MODEL="$SKIP_MODEL" \
  bash <<'EOF'
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive
sudo apt-get update -y
sudo apt-get install -y \
  python3 python3-venv python3-pip build-essential \
  libopenblas-dev rng-tools curl

mkdir -p "$(dirname "$CONFIG_PATH")"
mkdir -p "$(dirname "$MODEL_PATH")"

cp "$REMOTE_DIR/deploy/raspberry-pi/config.yaml" "$CONFIG_PATH"

if [[ "$SKIP_MODEL" -eq 0 && ! -f "$MODEL_PATH" ]]; then
  echo "[andyria] downloading tiny model to $MODEL_PATH"
  curl -L --fail --retry 3 -o "$MODEL_PATH" "$MODEL_URL"
fi

cd "$REMOTE_DIR"
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e "python/[llm]"

SERVICE_PATH="/etc/systemd/system/andyria.service"
sudo tee "$SERVICE_PATH" >/dev/null <<UNIT
[Unit]
Description=Andyria Edge Node
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$PI_USER
WorkingDirectory=$REMOTE_DIR
ExecStart=$REMOTE_DIR/.venv/bin/python -m andyria serve --config $CONFIG_PATH
Restart=always
RestartSec=3
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable andyria.service
sudo systemctl restart andyria.service

sleep 2
sudo systemctl --no-pager --full status andyria.service | sed -n '1,20p'
curl -fsS "http://127.0.0.1:7700/v1/status" >/tmp/andyria-status.json
cat /tmp/andyria-status.json
EOF

log "Verifying remote endpoint from local machine"
if curl -fsS "http://${PI_HOST}:7700/v1/status" >/dev/null 2>&1; then
  log "Deployment healthy at http://${PI_HOST}:7700"
else
  warn "Remote endpoint not reachable from this machine. Check firewall/router, but service is installed on Pi."
fi

log "Done."
