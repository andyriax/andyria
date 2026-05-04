/**
 * andyria_node — ESP32 edge participant firmware
 *
 * Capabilities:
 *   - Generates a persistent Ed25519-grade node identity (hardware TRNG, NVS)
 *   - Exports device key on first-boot pairing (TOFU over trusted USB)
 *   - HMAC-SHA256 challenge-response attestation on every session
 *   - Hardware entropy stream (TRNG + ADC noise + timing jitter) → host DAG
 *   - Periodic heartbeat with uptime, free heap, WiFi RSSI
 *   - Responds to: hello | ping | challenge
 *   - All messages are JSON Lines (newline-delimited) at 115200 baud
 *
 * Dependencies (install via Arduino Library Manager or PlatformIO):
 *   - ArduinoJson  >= 7.x   (bblanchon/ArduinoJson)
 *   - mbedTLS      built-in with ESP32 Arduino core >= 2.x
 *
 * Protocol:
 *   Host → {"cmd":"hello","nonce":"<64-hex>"}
 *   MCU  → {"type":"ident","node_id":"<32-hex>","firmware":"andyria-mcu-v1",
 *            "caps":["entropy","heartbeat","hmac_sha256"],
 *            "key_export":"<64-hex>",   ← FIRST BOOT ONLY (null after pairing)
 *            "hmac":"<64-hex>"}         ← HMAC-SHA256(device_key, nonce_bytes)
 *
 *   Host → {"cmd":"challenge","token":"<64-hex>"}
 *   MCU  → {"type":"response","token_echo":"<64-hex>","hmac":"<64-hex>"}
 *
 *   MCU  → {"type":"entropy","bytes":"<128-hex>","ts_us":<int>}  (every ~5 s)
 *   MCU  → {"type":"heartbeat","uptime_ms":<int>,"free_heap":<int>,"rssi":<int>}
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#include <Arduino.h>
#include <ArduinoJson.h>
#include <Preferences.h>
#include <mbedtls/md.h>
#include <esp_random.h>

// ── Configuration ────────────────────────────────────────────────────────────
#define SERIAL_BAUD       115200
#define HEARTBEAT_MS      10000UL
#define ENTROPY_MS         5000UL
#define ENTROPY_ADC_PIN      34   // floating ADC pin (no pullup/pulldown)
#define ENTROPY_ADC_SAMPLES  64
#define KEY_LEN              32   // bytes
#define NODE_ID_LEN          16   // bytes → 32-hex chars

// ── Globals ───────────────────────────────────────────────────────────────────
static Preferences prefs;
static uint8_t  g_device_key[KEY_LEN];
static uint8_t  g_node_id[NODE_ID_LEN];
static bool     g_paired          = false;   // true after host confirmed key
static unsigned long g_last_hb    = 0;
static unsigned long g_last_ent   = 0;

// ── Utility: bytes → lowercase hex ───────────────────────────────────────────
static String hexEncode(const uint8_t* buf, size_t len) {
  String out;
  out.reserve(len * 2);
  for (size_t i = 0; i < len; i++) {
    char hex[3];
    snprintf(hex, sizeof(hex), "%02x", buf[i]);
    out += hex;
  }
  return out;
}

// ── Utility: hex string → bytes (returns false if invalid) ───────────────────
static bool hexDecode(const String& hex, uint8_t* out, size_t expected) {
  if (hex.length() != expected * 2) return false;
  for (size_t i = 0; i < expected; i++) {
    char hi = hex[i * 2];
    char lo = hex[i * 2 + 1];
    auto fromHex = [](char c) -> int {
      if (c >= '0' && c <= '9') return c - '0';
      if (c >= 'a' && c <= 'f') return c - 'a' + 10;
      if (c >= 'A' && c <= 'F') return c - 'A' + 10;
      return -1;
    };
    int h = fromHex(hi), l = fromHex(lo);
    if (h < 0 || l < 0) return false;
    out[i] = (uint8_t)((h << 4) | l);
  }
  return true;
}

// ── HMAC-SHA256 ───────────────────────────────────────────────────────────────
static bool hmacSha256(const uint8_t* key, size_t klen,
                       const uint8_t* msg, size_t mlen,
                       uint8_t out[32]) {
  const mbedtls_md_info_t* info = mbedtls_md_info_from_type(MBEDTLS_MD_SHA256);
  mbedtls_md_context_t ctx;
  mbedtls_md_init(&ctx);
  if (mbedtls_md_setup(&ctx, info, 1) != 0) {
    mbedtls_md_free(&ctx);
    return false;
  }
  mbedtls_md_hmac_starts(&ctx, key, klen);
  mbedtls_md_hmac_update(&ctx, msg, mlen);
  mbedtls_md_hmac_finish(&ctx, out);
  mbedtls_md_free(&ctx);
  return true;
}

// ── Entropy collection ────────────────────────────────────────────────────────
static void collectEntropy(uint8_t* buf, size_t len) {
  // 1. Hardware TRNG (ESP32 built-in — fastest, highest quality)
  for (size_t i = 0; i + 3 < len; i += 4) {
    uint32_t r = esp_random();
    memcpy(buf + i, &r, 4);
  }
  // 2. XOR in ADC noise (floating pin thermal/shot noise)
  for (int i = 0; i < ENTROPY_ADC_SAMPLES && i < (int)len; i++) {
    buf[i % len] ^= (uint8_t)(analogRead(ENTROPY_ADC_PIN) & 0xFF);
  }
  // 3. XOR in timing jitter (loop scheduling noise at µs resolution)
  for (size_t i = 0; i < len; i++) {
    buf[i] ^= (uint8_t)(micros() & 0xFF);
  }
}

// ── Identity persistence ──────────────────────────────────────────────────────
static void loadOrCreateIdentity() {
  prefs.begin("andyria", false);

  bool has_id  = prefs.isKey("node_id");
  bool has_key = prefs.isKey("dev_key");
  g_paired     = prefs.getBool("paired", false);

  if (has_id && has_key) {
    prefs.getBytes("node_id", g_node_id, NODE_ID_LEN);
    prefs.getBytes("dev_key", g_device_key, KEY_LEN);
  } else {
    // First boot: generate from hardware TRNG
    collectEntropy(g_node_id, NODE_ID_LEN);
    collectEntropy(g_device_key, KEY_LEN);
    prefs.putBytes("node_id", g_node_id, NODE_ID_LEN);
    prefs.putBytes("dev_key", g_device_key, KEY_LEN);
    g_paired = false;
    prefs.putBool("paired", false);
  }
  prefs.end();
}

static void markPaired() {
  if (!g_paired) {
    g_paired = true;
    prefs.begin("andyria", false);
    prefs.putBool("paired", true);
    prefs.end();
  }
}

// ── Message handlers ──────────────────────────────────────────────────────────
static void handleHello(const String& nonce_hex) {
  uint8_t nonce[32];
  if (!hexDecode(nonce_hex, nonce, 32)) {
    Serial.println(F("{\"type\":\"error\",\"msg\":\"bad nonce\"}"));
    return;
  }

  uint8_t hmac_out[32];
  hmacSha256(g_device_key, KEY_LEN, nonce, 32, hmac_out);

  JsonDocument doc;
  doc["type"]    = "ident";
  doc["node_id"] = hexEncode(g_node_id, NODE_ID_LEN);
  doc["firmware"]= "andyria-mcu-v1";
  JsonArray caps = doc["caps"].to<JsonArray>();
  caps.add("entropy");
  caps.add("heartbeat");
  caps.add("hmac_sha256");
  doc["nonce_echo"] = nonce_hex;
  doc["hmac"]       = hexEncode(hmac_out, 32);

  // Export the raw device key only on first-boot pairing (TOFU).
  // After the host calls back to confirm, we mark paired and stop exporting.
  if (!g_paired) {
    doc["key_export"] = hexEncode(g_device_key, KEY_LEN);
  } else {
    doc["key_export"] = nullptr;  // null after pairing
  }

  String out;
  serializeJson(doc, out);
  Serial.println(out);
}

static void handleChallenge(const String& token_hex) {
  uint8_t token[32];
  if (!hexDecode(token_hex, token, 32)) {
    Serial.println(F("{\"type\":\"error\",\"msg\":\"bad token\"}"));
    return;
  }

  uint8_t hmac_out[32];
  hmacSha256(g_device_key, KEY_LEN, token, 32, hmac_out);

  JsonDocument doc;
  doc["type"]       = "response";
  doc["token_echo"] = token_hex;
  doc["hmac"]       = hexEncode(hmac_out, 32);
  String out;
  serializeJson(doc, out);
  Serial.println(out);
}

static void handlePairedAck() {
  markPaired();
  Serial.println(F("{\"type\":\"ack\",\"msg\":\"paired\"}"));
}

static void handlePing() {
  JsonDocument doc;
  doc["type"]  = "pong";
  doc["ts_us"] = (unsigned long)micros();
  String out;
  serializeJson(doc, out);
  Serial.println(out);
}

// ── Periodic emissions ────────────────────────────────────────────────────────
static void emitEntropy() {
  uint8_t entropy_buf[64];
  collectEntropy(entropy_buf, sizeof(entropy_buf));

  JsonDocument doc;
  doc["type"]  = "entropy";
  doc["bytes"] = hexEncode(entropy_buf, sizeof(entropy_buf));
  doc["ts_us"] = (unsigned long)micros();
  String out;
  serializeJson(doc, out);
  Serial.println(out);
}

static void emitHeartbeat() {
  JsonDocument doc;
  doc["type"]      = "heartbeat";
  doc["uptime_ms"] = (unsigned long)millis();
  doc["free_heap"] = (int)ESP.getFreeHeap();
  // RSSI only meaningful if WiFi is active; -100 signals "no WiFi"
  doc["rssi"]      = -100;
  String out;
  serializeJson(doc, out);
  Serial.println(out);
}

// ── Command dispatcher ────────────────────────────────────────────────────────
static void dispatchCommand(const String& line) {
  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, line);
  if (err) return;  // silently drop malformed input

  const char* cmd = doc["cmd"] | "";

  if (strcmp(cmd, "hello") == 0) {
    handleHello(doc["nonce"] | "");
  } else if (strcmp(cmd, "challenge") == 0) {
    handleChallenge(doc["token"] | "");
  } else if (strcmp(cmd, "paired_ack") == 0) {
    handlePairedAck();
  } else if (strcmp(cmd, "ping") == 0) {
    handlePing();
  }
  // Unknown commands are silently ignored
}

// ── Arduino entry points ──────────────────────────────────────────────────────
void setup() {
  Serial.begin(SERIAL_BAUD);
  while (!Serial) delay(10);

  analogReadResolution(12);   // max resolution for ADC noise
  analogSetAttenuation(ADC_11db);

  loadOrCreateIdentity();

  // Startup banner → host can detect device presence
  Serial.println(F("{\"type\":\"ready\",\"firmware\":\"andyria-mcu-v1\"}"));
}

void loop() {
  // ── Read incoming commands ────────────────────────────────────────────────
  if (Serial.available()) {
    String line = Serial.readStringUntil('\n');
    line.trim();
    if (line.length() > 0) {
      dispatchCommand(line);
    }
  }

  // ── Periodic entropy emission ─────────────────────────────────────────────
  unsigned long now = millis();
  if (now - g_last_ent >= ENTROPY_MS) {
    g_last_ent = now;
    emitEntropy();
  }

  // ── Periodic heartbeat ────────────────────────────────────────────────────
  if (now - g_last_hb >= HEARTBEAT_MS) {
    g_last_hb = now;
    emitHeartbeat();
  }
}
