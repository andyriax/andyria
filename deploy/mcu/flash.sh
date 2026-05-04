#!/usr/bin/env bash
# deploy/mcu/flash.sh — One-command firmware flash for Andyria MCU nodes
#
# Usage:
#   ./deploy/mcu/flash.sh --target esp32    [--port /dev/ttyUSB0]
#   ./deploy/mcu/flash.sh --target arduino  [--port /dev/ttyACM0]
#   ./deploy/mcu/flash.sh --target esp32    --auto     # auto-detect port
#   ./deploy/mcu/flash.sh --install-tools              # install arduino-cli only
#
# Behaviour:
#   1. Installs arduino-cli if missing (silent, to ~/.local/bin)
#   2. Installs the required board core (esp32:esp32 or arduino:avr)
#   3. Installs library dependencies (ArduinoJson for esp32 target)
#   4. Compiles the sketch
#   5. Flashes to the target port
#   6. Opens a brief serial monitor to confirm the "ready" banner
#
# Exit codes:
#   0  success
#   1  usage / argument error
#   2  dependency installation failed
#   3  compile failed
#   4  flash failed
#   5  no device found on auto-detect
#
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ARDUINO_CLI_VER="1.0.4"
ARDUINO_CLI_BIN="${HOME}/.local/bin/arduino-cli"

TARGET=""
PORT=""
AUTO_PORT=0
INSTALL_TOOLS_ONLY=0
BOARD_FQBN=""
SKETCH_DIR=""

# ── Colours ──────────────────────────────────────────────────────────────────
BOLD=$'\033[1m'; CYAN=$'\033[36m'; GREEN=$'\033[32m'
YELLOW=$'\033[33m'; RED=$'\033[31m'; RESET=$'\033[0m'
log()  { printf "${CYAN}[andyria-flash]${RESET} %s\n" "$*"; }
ok()   { printf "${GREEN}[andyria-flash] ✓${RESET} %s\n" "$*"; }
warn() { printf "${YELLOW}[andyria-flash] ⚠${RESET} %s\n" "$*" >&2; }
die()  { printf "${RED}[andyria-flash] ✗${RESET} %s\n" "$*" >&2; exit "${2:-1}"; }

# ── Parse args ────────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --target)         TARGET="$2"; shift 2 ;;
    --port)           PORT="$2";   shift 2 ;;
    --auto)           AUTO_PORT=1; shift   ;;
    --install-tools)  INSTALL_TOOLS_ONLY=1; shift ;;
    *) die "Unknown option: $1" 1 ;;
  esac
done

# ── Install arduino-cli ───────────────────────────────────────────────────────
install_arduino_cli() {
  if command -v arduino-cli >/dev/null 2>&1; then
    ARDUINO_CLI_BIN="$(command -v arduino-cli)"
    ok "arduino-cli already installed: ${ARDUINO_CLI_BIN}"
    return
  fi
  if [[ -x "${ARDUINO_CLI_BIN}" ]]; then
    ok "arduino-cli already at ${ARDUINO_CLI_BIN}"
    return
  fi

  log "Installing arduino-cli v${ARDUINO_CLI_VER} to ${HOME}/.local/bin"
  mkdir -p "${HOME}/.local/bin"

  local arch
  arch="$(uname -m)"
  case "${arch}" in
    x86_64)  arch="64bit" ;;
    aarch64) arch="ARM64" ;;
    armv7*)  arch="ARMv7" ;;
    *)       die "Unsupported arch: ${arch}" 2 ;;
  esac

  local os
  os="$(uname -s)"
  case "${os}" in
    Linux)  os="Linux" ;;
    Darwin) os="macOS" ;;
    *)      die "Unsupported OS: ${os}" 2 ;;
  esac

  local url="https://downloads.arduino.cc/arduino-cli/arduino-cli_${ARDUINO_CLI_VER}_${os}_${arch}.tar.gz"
  curl -fsSL "${url}" | tar -xzC "${HOME}/.local/bin" arduino-cli \
    || die "Failed to download arduino-cli from ${url}" 2

  ok "arduino-cli installed"
}

# ── Install board cores and libraries ────────────────────────────────────────
install_board() {
  log "Updating board index…"
  "${ARDUINO_CLI_BIN}" core update-index --additional-urls \
    "https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json" \
    >/dev/null 2>&1 || true

  case "${TARGET}" in
    esp32)
      log "Installing esp32:esp32 core (this may take a few minutes on first run)…"
      "${ARDUINO_CLI_BIN}" core install esp32:esp32 \
        --additional-urls "https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json" \
        2>&1 | tail -3
      log "Installing ArduinoJson library…"
      "${ARDUINO_CLI_BIN}" lib install "ArduinoJson" 2>&1 | tail -2
      BOARD_FQBN="esp32:esp32:esp32"
      SKETCH_DIR="${SCRIPT_DIR}/esp32/andyria_node"
      ;;
    arduino)
      log "Installing arduino:avr core…"
      "${ARDUINO_CLI_BIN}" core install arduino:avr 2>&1 | tail -2
      BOARD_FQBN="arduino:avr:uno"
      SKETCH_DIR="${SCRIPT_DIR}/arduino/andyria_node"
      ;;
    *) die "Unknown target: '${TARGET}'. Use --target esp32 or --target arduino" 1 ;;
  esac
}

# ── Auto-detect serial port ───────────────────────────────────────────────────
detect_port() {
  log "Auto-detecting MCU on serial ports…"
  local candidates
  # Check Linux/WSL tty paths first, then macOS
  candidates=$(ls /dev/ttyUSB* /dev/ttyACM* /dev/cu.usbserial* /dev/cu.usbmodem* 2>/dev/null || true)
  if [[ -z "${candidates}" ]]; then
    die "No serial ports found. Connect the device and retry, or use --port /dev/ttyXXX" 5
  fi
  # Prefer /dev/ttyACM (Arduino/ESP32 CDC) over /dev/ttyUSB (CH340/CP210x)
  PORT=$(echo "${candidates}" | grep -E 'ttyACM|cu.usbmodem' | head -1 || echo "${candidates}" | head -1)
  ok "Detected port: ${PORT}"
}

# ── Main ──────────────────────────────────────────────────────────────────────
install_arduino_cli

if [[ "${INSTALL_TOOLS_ONLY}" -eq 1 ]]; then
  ok "Tools installed. Run again with --target to flash a device."
  exit 0
fi

[[ -n "${TARGET}" ]] || die "Specify --target esp32 or --target arduino" 1

install_board

if [[ -z "${PORT}" ]]; then
  if [[ "${AUTO_PORT}" -eq 1 ]]; then
    detect_port
  else
    die "Specify --port /dev/ttyXXX or use --auto to detect automatically" 1
  fi
fi

log "Compiling sketch for ${BOARD_FQBN}…"
"${ARDUINO_CLI_BIN}" compile --fqbn "${BOARD_FQBN}" "${SKETCH_DIR}" \
  || die "Compilation failed. Check sketch and board core." 3
ok "Compiled"

log "Flashing to ${PORT}…"
"${ARDUINO_CLI_BIN}" upload --fqbn "${BOARD_FQBN}" --port "${PORT}" "${SKETCH_DIR}" \
  || die "Flash failed. Is the device in bootloader mode? Try pressing BOOT/RESET." 4
ok "Flashed"

log "Verifying firmware — waiting for ready beacon (5 s)…"
# Use python3 serial monitor for portable newline handling
if command -v python3 >/dev/null 2>&1; then
  python3 - "${PORT}" <<'PY' &
import sys, serial, time
port = sys.argv[1]
try:
    s = serial.Serial(port, 115200, timeout=5)
    deadline = time.time() + 5
    while time.time() < deadline:
        line = s.readline().decode("utf-8", errors="replace").strip()
        if line:
            print(f"  MCU → {line}")
        if '"type":"ready"' in line:
            print("  ✓ firmware confirmed ready")
            break
    s.close()
except Exception as e:
    print(f"  (serial monitor skipped: {e})")
PY
  sleep 6
  kill %1 2>/dev/null || true
else
  warn "python3 not found — skipping serial confirmation. Run the bridge to verify."
fi

echo ""
printf "${BOLD}${GREEN}Done.${RESET} Device is flashed and ready.\n"
echo ""
echo "  Next step — start the host bridge:"
echo "    python -m andyria.mcu_bridge --port ${PORT} --api http://localhost:7700"
echo ""
echo "  Or let the bridge auto-discover all connected devices:"
echo "    python -m andyria.mcu_bridge --auto --api http://localhost:7700"
