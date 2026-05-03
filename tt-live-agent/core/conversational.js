/**
 * core/conversational.js
 * Conversational mode manager.
 *
 * Controls whether the agent responds to ANY chat message (on) or only
 * to explicit commands + fast-path intent templates (off).
 *
 * Commands (handled in router.js):
 *   /conversational:on      → enable full LLM conversational mode
 *   /conversational:off     → disable (commands + fast-path only)
 *   /conversational:toggle  → flip current state
 *   /conversational:status  → report current state
 *
 * Per-user rate limiting prevents response spam in high-traffic chat.
 */

'use strict';

const { appendEvent } = require('./db');

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
let _enabled = false;              // default OFF — only commands + fast-path

// Per-user last-response timestamps for rate limiting
const _userLastResponse = new Map();

// Config (overridden by configure())
let _cfg = {
  rateLimitMs       : 8_000,   // min ms between conversational replies per user
  globalRateLimitMs : 500,     // min ms between ANY conversational reply (global)
  maxQueuedPerUser  : 1,       // drop backlogged conv messages beyond this
};

let _globalLastResponse = 0;

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Apply config from config.json's `conversational` block.
 * @param {object} cfg
 * @param {boolean} [cfg.default]        - start enabled (default: false)
 * @param {number}  [cfg.rateLimitMs]    - per-user rate limit
 * @param {number}  [cfg.globalRateLimitMs]
 */
function configure(cfg = {}) {
  if (cfg.default === true) _enabled = true;
  if (typeof cfg.rateLimitMs === 'number')       _cfg.rateLimitMs       = cfg.rateLimitMs;
  if (typeof cfg.globalRateLimitMs === 'number') _cfg.globalRateLimitMs = cfg.globalRateLimitMs;
}

/** Is conversational mode currently enabled? */
function isEnabled() {
  return _enabled;
}

/** Enable conversational mode. Returns new state. */
function enable(invoker = 'system') {
  const prev = _enabled;
  _enabled = true;
  if (!prev) {
    appendEvent('conversational:mode', 'CONVERSATIONAL_ENABLED', { invoker });
    console.log(`[conversational] Mode ON — all chat messages will receive LLM responses`);
  }
  return true;
}

/** Disable conversational mode. Returns new state. */
function disable(invoker = 'system') {
  const prev = _enabled;
  _enabled = false;
  if (prev) {
    appendEvent('conversational:mode', 'CONVERSATIONAL_DISABLED', { invoker });
    console.log(`[conversational] Mode OFF — responding to commands and fast-path intents only`);
  }
  return false;
}

/** Toggle conversational mode. Returns new state. */
function toggle(invoker = 'system') {
  return _enabled ? disable(invoker) : enable(invoker);
}

/**
 * Check whether a conversational reply is permitted for this user right now.
 * Enforces per-user rate limit + global cooldown.
 *
 * @param {string} user
 * @returns {{ allowed: boolean, reason?: string }}
 */
function checkRateLimit(user) {
  const now = Date.now();

  // Global rate limiter
  if (now - _globalLastResponse < _cfg.globalRateLimitMs) {
    return { allowed: false, reason: 'global_rate_limit' };
  }

  // Per-user rate limiter
  const last = _userLastResponse.get(user) || 0;
  if (now - last < _cfg.rateLimitMs) {
    return { allowed: false, reason: 'user_rate_limit', remainingMs: _cfg.rateLimitMs - (now - last) };
  }

  return { allowed: true };
}

/**
 * Mark that a conversational reply was just sent to this user.
 * Must be called after successfully dispatching a reply.
 * @param {string} user
 */
function markResponded(user) {
  const now = Date.now();
  _globalLastResponse = now;
  _userLastResponse.set(user, now);
}

/**
 * Purge stale per-user rate limit entries (call periodically to avoid memory leak).
 */
function pruneCache() {
  const cutoff = Date.now() - _cfg.rateLimitMs * 10;
  for (const [user, ts] of _userLastResponse) {
    if (ts < cutoff) _userLastResponse.delete(user);
  }
}

// Auto-prune every 5 minutes
setInterval(pruneCache, 5 * 60 * 1000).unref();

/**
 * Full status snapshot for /stats and logging.
 */
function status() {
  return {
    enabled         : _enabled,
    rateLimitMs     : _cfg.rateLimitMs,
    globalRateLimitMs: _cfg.globalRateLimitMs,
    trackedUsers    : _userLastResponse.size,
  };
}

module.exports = {
  configure,
  isEnabled,
  enable,
  disable,
  toggle,
  checkRateLimit,
  markResponded,
  status,
};
