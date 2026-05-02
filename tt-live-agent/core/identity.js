/**
 * core/identity.js
 * SHA3-512 identity sealing for the Jetstreamin DAG capsule.
 * Every agent, event, and command is sealed with a deterministic
 * SHA3-512 fingerprint derived from its content + timestamp nonce.
 */

'use strict';

const crypto = require('crypto');
const fs     = require('fs');
const path   = require('path');

const CAPSULE_PATH = path.join(__dirname, '..', 'capsule.json');

// ---------------------------------------------------------------------------
// SHA3-512 helpers
// ---------------------------------------------------------------------------

/**
 * Hash arbitrary data with SHA3-512.
 * @param {string|Buffer} data
 * @returns {string} hex digest
 */
function sha3(data) {
  return crypto.createHash('sha3-512').update(data).digest('hex');
}

/**
 * Seal an object — returns { ...obj, _seal, _ts } where _seal is SHA3-512
 * of JSON(obj) + nonce timestamp. Immutable once returned.
 * @param {object} obj
 * @returns {object}
 */
function seal(obj) {
  const ts   = Date.now();
  const raw  = JSON.stringify(obj) + ts.toString();
  const hash = sha3(raw);
  return Object.freeze({ ...obj, _ts: ts, _seal: hash });
}

/**
 * Verify a previously sealed object.
 * @param {object} sealed
 * @returns {boolean}
 */
function verify(sealed) {
  const { _seal, _ts, ...rest } = sealed;
  if (!_seal || !_ts) return false;
  const raw      = JSON.stringify(rest) + _ts.toString();
  const expected = sha3(raw);
  return expected === _seal;
}

// ---------------------------------------------------------------------------
// Capsule identity (node-level identity document)
// ---------------------------------------------------------------------------

function loadOrCreateCapsule() {
  if (fs.existsSync(CAPSULE_PATH)) {
    try {
      return JSON.parse(fs.readFileSync(CAPSULE_PATH, 'utf8'));
    } catch (_) { /* fall through to create */ }
  }

  const id = crypto.randomUUID();
  const capsule = seal({
    capsule_id : id,
    name       : 'tt-live-agent',
    version    : '1.0.0',
    created_at : Date.now(),
    platform   : 'tiktok-live',
    runtime    : 'jetstreamin-dag-capsule',
  });

  fs.writeFileSync(CAPSULE_PATH, JSON.stringify(capsule, null, 2));
  console.log(`[identity] Capsule created: ${id}`);
  return capsule;
}

module.exports = { sha3, seal, verify, loadOrCreateCapsule };
