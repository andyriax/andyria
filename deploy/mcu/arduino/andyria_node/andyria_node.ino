/**
 * andyria_node — Arduino Uno/Nano/Mega fallback firmware
 *
 * Capabilities (reduced vs ESP32 — no hardware TRNG, no WiFi):
 *   - Persistent node identity in EEPROM (seeded from ADC noise + timing)
 *   - XOR challenge-response (NOT cryptographic — clearly documented)
 *   - ADC noise + timing jitter entropy stream
 *   - Periodic heartbeat with uptime
 *
 * Protocol: identical JSON Lines message format as ESP32 variant.
 * caps field will include "entropy","heartbeat","xor_response"
 * and will NOT include "hmac_sha256".
 *
 * NOTE: The XOR challenge-response provides replay-resistant session
 * binding but is NOT a cryptographic MAC. Use the ESP32 variant for
 * any deployment requiring strong attestation.
 *
 * No external libraries required — uses only standard Arduino SDK.
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#include <Arduino.h>
#include <EEPROM.h>

// ── Configuration ─────────────────────────────────────────────────────────────
#define SERIAL_BAUD       115200
#define HEARTBEAT_MS      10000UL
#define ENTROPY_MS         5000UL
#define ENTROPY_ADC_PIN      A0   // floating analog pin for noise
#define ENTROPY_ADC_SAMPLES  32
#define NODE_ID_LEN          8    // bytes → 16-hex chars (EEPROM-constrained)
#define EEPROM_MAGIC      0xA9    // sentinel to detect first boot

// EEPROM layout:
//   addr 0          : EEPROM_MAGIC byte
//   addr 1..NODE_ID_LEN : node_id bytes

// ── Minimal JSON output (no ArduinoJson — memory budget) ─────────────────────
static void printHex(const uint8_t* buf, size_t len) {
  for (size_t i = 0; i < len; i++) {
    if (buf[i] < 0x10) Serial.print('0');
    Serial.print(buf[i], HEX);
  }
}

// ── Globals ───────────────────────────────────────────────────────────────────
static uint8_t g_node_id[NODE_ID_LEN];

// ── Entropy helpers ───────────────────────────────────────────────────────────
static void collectEntropyInto(uint8_t* buf, size_t len) {
  // ADC noise — low bits of floating pin
  for (size_t i = 0; i < len; i++) buf[i] = 0;
  for (int s = 0; s < ENTROPY_ADC_SAMPLES; s++) {
    buf[s % len] ^= (uint8_t)(analogRead(ENTROPY_ADC_PIN) & 0xFF);
  }
  // Timing jitter — low bits of micros()
  for (size_t i = 0; i < len; i++) {
    buf[i] ^= (uint8_t)(micros() & 0xFF);
    delayMicroseconds(3);  // let the counter advance between reads
  }
}

// ── Identity persistence ──────────────────────────────────────────────────────
static void loadOrCreateIdentity() {
  if (EEPROM.read(0) != EEPROM_MAGIC) {
    // First boot — seed node_id from ADC noise + timing
    collectEntropyInto(g_node_id, NODE_ID_LEN);
    EEPROM.write(0, EEPROM_MAGIC);
    for (size_t i = 0; i < NODE_ID_LEN; i++) {
      EEPROM.write(1 + i, g_node_id[i]);
    }
  } else {
    for (size_t i = 0; i < NODE_ID_LEN; i++) {
      g_node_id[i] = EEPROM.read(1 + i);
    }
  }
}

// ── Hex decode helper ─────────────────────────────────────────────────────────
static uint8_t fromHexChar(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  return 0;
}

// ── Message handlers ──────────────────────────────────────────────────────────

// Minimal JSON field extractor — finds "key":"value" pairs in the line.
// Only handles string values; good enough for our fixed protocol.
static String extractField(const String& json, const char* key) {
  String search = "\"";
  search += key;
  search += "\":\"";
  int idx = json.indexOf(search);
  if (idx < 0) return "";
  int start = idx + search.length();
  int end   = json.indexOf('"', start);
  if (end < 0) return "";
  return json.substring(start, end);
}

static void handleHello(const String& nonce_hex) {
  // XOR-based response: for each byte pair of nonce, XOR with corresponding
  // node_id byte (wrapped). This binds the response to this device's identity
  // and the nonce, but is NOT a secure MAC.
  Serial.print(F("{\"type\":\"ident\",\"node_id\":\""));
  printHex(g_node_id, NODE_ID_LEN);
  Serial.print(F("\",\"firmware\":\"andyria-mcu-v1\",\"caps\":[\"entropy\",\"heartbeat\",\"xor_response\"],\"nonce_echo\":\""));
  Serial.print(nonce_hex);
  Serial.print(F("\",\"key_export\":null,\"hmac\":\""));

  // Build XOR response over first 32 bytes of nonce (padded if shorter)
  for (int i = 0; i < 32; i++) {
    uint8_t nb = 0;
    if ((size_t)(i * 2 + 1) < (size_t)nonce_hex.length()) {
      nb = (fromHexChar(nonce_hex[i * 2]) << 4) | fromHexChar(nonce_hex[i * 2 + 1]);
    }
    uint8_t xored = nb ^ g_node_id[i % NODE_ID_LEN];
    if (xored < 0x10) Serial.print('0');
    Serial.print(xored, HEX);
  }
  Serial.println(F("\"}"));
}

static void handleChallenge(const String& token_hex) {
  Serial.print(F("{\"type\":\"response\",\"token_echo\":\""));
  Serial.print(token_hex);
  Serial.print(F("\",\"hmac\":\""));
  // Same XOR response over token bytes
  for (int i = 0; i < 32; i++) {
    uint8_t tb = 0;
    if ((size_t)(i * 2 + 1) < (size_t)token_hex.length()) {
      tb = (fromHexChar(token_hex[i * 2]) << 4) | fromHexChar(token_hex[i * 2 + 1]);
    }
    uint8_t xored = tb ^ g_node_id[i % NODE_ID_LEN];
    if (xored < 0x10) Serial.print('0');
    Serial.print(xored, HEX);
  }
  Serial.println(F("\"}"));
}

static void handlePairedAck() {
  // Arduino has no persistent paired flag (limited EEPROM writes).
  // Respond with ack; host tracks pairing state on its side.
  Serial.println(F("{\"type\":\"ack\",\"msg\":\"paired\"}"));
}

static void handlePing() {
  Serial.print(F("{\"type\":\"pong\",\"ts_us\":"));
  Serial.print((unsigned long)micros());
  Serial.println(F("}"));
}

static void emitEntropy() {
  uint8_t buf[32];
  collectEntropyInto(buf, sizeof(buf));
  Serial.print(F("{\"type\":\"entropy\",\"bytes\":\""));
  printHex(buf, sizeof(buf));
  Serial.print(F("\",\"ts_us\":"));
  Serial.print((unsigned long)micros());
  Serial.println(F("}"));
}

static void emitHeartbeat() {
  Serial.print(F("{\"type\":\"heartbeat\",\"uptime_ms\":"));
  Serial.print((unsigned long)millis());
  // Arduino Uno has no free-heap introspection; report -1
  Serial.print(F(",\"free_heap\":-1,\"rssi\":-100}"));
  Serial.println();
}

// ── Command dispatcher (no JSON library — manual substring matching) ──────────
static void dispatchCommand(const String& line) {
  // Extract "cmd" field
  String cmd = extractField(line, "cmd");

  if (cmd == "hello") {
    handleHello(extractField(line, "nonce"));
  } else if (cmd == "challenge") {
    handleChallenge(extractField(line, "token"));
  } else if (cmd == "paired_ack") {
    handlePairedAck();
  } else if (cmd == "ping") {
    handlePing();
  }
}

// ── Arduino entry points ──────────────────────────────────────────────────────
void setup() {
  Serial.begin(SERIAL_BAUD);
  while (!Serial) {}

  loadOrCreateIdentity();
  Serial.println(F("{\"type\":\"ready\",\"firmware\":\"andyria-mcu-v1\"}"));
}

void loop() {
  static String cmd_buf;

  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n') {
      cmd_buf.trim();
      if (cmd_buf.length() > 0) {
        dispatchCommand(cmd_buf);
        cmd_buf = "";
      }
    } else {
      cmd_buf += c;
    }
  }

  unsigned long now = millis();

  static unsigned long last_ent = 0;
  if (now - last_ent >= ENTROPY_MS) {
    last_ent = now;
    emitEntropy();
  }

  static unsigned long last_hb = 0;
  if (now - last_hb >= HEARTBEAT_MS) {
    last_hb = now;
    emitHeartbeat();
  }
}
