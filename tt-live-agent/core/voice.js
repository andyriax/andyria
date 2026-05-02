/**
 * core/voice.js
 * Tri-layer voice synthesis engine.
 *
 * Layer 1 — Primary:  full-speed TTS via `say` (or platform native)
 * Layer 2 — Echo:     delayed, lower-gain repeat (reinforcement layer)
 * Layer 3 — Whisper:  ultra-low-gain murmur for subliminal rhythm
 *
 * If `say` is not available (CI / headless), speech is no-op'd with
 * a console log so the agent can still run in dry-run / test mode.
 *
 * Config (from config.json voice block):
 * {
 *   "enabled": true,
 *   "tri_layer": true,
 *   "layer_primary": { "rate": 1.0 },
 *   "layer_echo":    { "delay_ms": 120, "gain": 0.45 },
 *   "layer_whisper": { "delay_ms": 280, "gain": 0.2 }
 * }
 */

'use strict';

let say;
try {
  say = require('say');
} catch (_) {
  say = null;
}

let _config = {
  enabled  : true,
  tri_layer: true,
  layer_primary: { rate: 1.0 },
  layer_echo   : { delay_ms: 120, gain: 0.45 },
  layer_whisper: { delay_ms: 280, gain: 0.2 },
};

let _personaVoice = null;   // set when persona loads

function configure(voiceConfig, persona) {
  if (voiceConfig) _config = { ..._config, ...voiceConfig };
  if (persona?.voice) _personaVoice = persona.voice;
}

// ---------------------------------------------------------------------------
// Internal TTS call
// ---------------------------------------------------------------------------

function _rawSpeak(text, voice, speed) {
  if (!say) {
    process.stdout.write(`[voice] ${text}\n`);
    return;
  }
  try {
    say.speak(text, voice || _personaVoice?.voice || null, speed || 1.0);
  } catch (e) {
    console.warn('[voice] TTS error:', e.message);
  }
}

// ---------------------------------------------------------------------------
// Public: speak with tri-layer synthesis
// ---------------------------------------------------------------------------

/**
 * Speak text through the tri-layer voice engine.
 * @param {string} text
 * @param {object} [opts]  - override voice/speed for this utterance
 */
function speak(text, opts = {}) {
  if (!_config.enabled) {
    process.stdout.write(`[voice:muted] ${text}\n`);
    return;
  }

  const primaryRate = (_config.layer_primary?.rate || 1.0) * (opts.rate || 1.0);
  const voice       = opts.voice || _personaVoice?.voice || null;

  // Layer 1 — Primary (immediate)
  _rawSpeak(text, voice, primaryRate);

  if (!_config.tri_layer) return;

  // Layer 2 — Echo (delayed, softer)
  const echoDelay   = _config.layer_echo?.delay_ms || 120;
  setTimeout(() => {
    _rawSpeak(text, voice, primaryRate * 0.85);
  }, echoDelay);

  // Layer 3 — Whisper (further delayed, barely audible)
  const whisperDelay = _config.layer_whisper?.delay_ms || 280;
  setTimeout(() => {
    // Whisper uses shorter text — first 6 words only
    const short = text.split(' ').slice(0, 6).join(' ');
    _rawSpeak(short, voice, primaryRate * 0.65);
  }, whisperDelay);
}

/**
 * Announce a revenue / gift event with an urgent voice style.
 * @param {string} user
 * @param {number} coins
 */
function announceGift(user, coins) {
  speak(`${user} just sent ${coins} coins! Let's go!`, { rate: 1.15 });
}

module.exports = { configure, speak, announceGift };
