/**
 * core/persona.js
 * Persona engine — loads JSON persona definitions, formats messages,
 * drives voice selection, and applies style transforms.
 *
 * Persona format:
 * {
 *   "name": "HypeGirl",
 *   "style": { "uppercase": true, "prefix": "🔥", "suffix": "💪" },
 *   "voice": { "engine": "say", "voice": "Samantha", "rate": 250 },
 *   "behavior": { "greet_all": true, "auto_hype_on_gift": true },
 *   "mission": "Drive energy and conversions on TikTok Live"
 * }
 */

'use strict';

const fs   = require('fs');
const path = require('path');

const PERSONA_DIR = path.join(__dirname, '..', 'personas');

let _current = null;

// ---------------------------------------------------------------------------
// Loader
// ---------------------------------------------------------------------------

function loadPersona(name) {
  const file = path.join(PERSONA_DIR, `${name}.json`);
  if (!fs.existsSync(file)) {
    throw new Error(`Persona not found: ${name}`);
  }
  _current = JSON.parse(fs.readFileSync(file, 'utf8'));
  console.log(`[persona] Loaded: ${_current.name}`);
  return _current;
}

function get() {
  if (!_current) loadPersona('default');
  return _current;
}

function updateCurrentPersona(patch = {}) {
  const current = get();
  if (patch.style && typeof patch.style === 'object') {
    current.style = { ...(current.style || {}), ...patch.style };
  }
  if (patch.voice && typeof patch.voice === 'object') {
    current.voice = { ...(current.voice || {}), ...patch.voice };
  }
  if (patch.behavior && typeof patch.behavior === 'object') {
    current.behavior = { ...(current.behavior || {}), ...patch.behavior };
  }

  for (const [k, v] of Object.entries(patch)) {
    if (k === 'style' || k === 'voice' || k === 'behavior') continue;
    current[k] = v;
  }
  return current;
}

function saveCurrentPersona(name) {
  const id = String(name || '').trim().toLowerCase().replace(/[^a-z0-9_-]+/g, '-');
  if (!id) {
    throw new Error('Persona name is required');
  }
  const file = path.join(PERSONA_DIR, `${id}.json`);
  fs.writeFileSync(file, JSON.stringify(get(), null, 2));
  return id;
}

// ---------------------------------------------------------------------------
// Message formatting
// ---------------------------------------------------------------------------

/**
 * Format a template string using the current persona style.
 * Replaces {user}, {count}, {coins}, etc.
 * @param {string} template
 * @param {object} vars  - substitution variables
 * @returns {string}
 */
function format(template, vars = {}) {
  const p = get();
  let text = template;

  // Variable substitution
  for (const [k, v] of Object.entries(vars)) {
    text = text.replaceAll(`{${k}}`, v);
  }

  // Style transforms
  if (p.style?.uppercase) text = text.toUpperCase();
  if (p.style?.prefix)    text = `${p.style.prefix} ${text}`;
  if (p.style?.suffix)    text = `${text} ${p.style.suffix}`;

  return text;
}

// ---------------------------------------------------------------------------
// List available personas
// ---------------------------------------------------------------------------

function listPersonas() {
  return fs.readdirSync(PERSONA_DIR)
    .filter(f => f.endsWith('.json'))
    .map(f => f.replace('.json', ''));
}

module.exports = { loadPersona, get, format, listPersonas, updateCurrentPersona, saveCurrentPersona };
