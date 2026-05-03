/**
 * core/ndjson.js
 * NDJSON (newline-delimited JSON) structured event logger.
 *
 * Produces machine-readable log files in the format required by the spec:
 *   { "ts": <iso>, "seq": <n>, "channel": "...", "event": "...", "payload": {...} }
 *
 * One log file per day, rotated at midnight.
 * Non-blocking — writes are async (fire-and-forget).
 * Falls back to console.warn on write error (never throws into caller).
 *
 * Usage:
 *   const ndjson = require('./ndjson');
 *   ndjson.configure({ dir: '/path/to/logs' });
 *   ndjson.write('tiktok:chat', 'CHAT_MESSAGE', { user: 'x', msg: 'hello' });
 */

'use strict';

const fs   = require('fs');
const path = require('path');

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------
let _dir        = path.join(__dirname, '..', 'logs');
let _enabled    = true;
let _maxFileMB  = 50;           // rotate if file exceeds this size
let _seq        = 0;

// Current write stream state
let _stream        = null;
let _streamDate    = '';        // YYYY-MM-DD of currently open file
let _streamSizeB   = 0;

/**
 * Apply config.
 * @param {object} cfg
 * @param {string}  [cfg.dir]        - log directory (default: ./logs)
 * @param {boolean} [cfg.enabled]    - disable to silence all file output
 * @param {number}  [cfg.maxFileMB]  - max file size before rotation
 */
function configure(cfg = {}) {
  if (cfg.dir)     _dir       = cfg.dir;
  if (cfg.enabled === false) _enabled = false;
  if (typeof cfg.maxFileMB === 'number') _maxFileMB = cfg.maxFileMB;

  if (_enabled) {
    try { fs.mkdirSync(_dir, { recursive: true }); } catch (_) {}
  }
}

// ---------------------------------------------------------------------------
// Stream management
// ---------------------------------------------------------------------------

function _dateString() {
  return new Date().toISOString().slice(0, 10); // YYYY-MM-DD
}

function _currentFilePath(date) {
  return path.join(_dir, `agent-${date}.ndjson`);
}

/**
 * Get (or create) the current write stream, rotating if date changed or file too large.
 * Returns null if logging is disabled.
 */
function _getStream() {
  if (!_enabled) return null;

  const today = _dateString();

  // Rotate on date change or size limit
  if (_stream && (_streamDate !== today || _streamSizeB > _maxFileMB * 1024 * 1024)) {
    try { _stream.end(); } catch (_) {}
    _stream = null;
  }

  if (!_stream) {
    try {
      fs.mkdirSync(_dir, { recursive: true });
      const filePath = _currentFilePath(today);
      _stream = fs.createWriteStream(filePath, { flags: 'a', encoding: 'utf8' });
      _stream.on('error', err => {
        console.warn('[ndjson] Stream error:', err.message);
        _stream = null;
      });
      // Seed size from existing file
      try {
        const stat = fs.statSync(filePath);
        _streamSizeB = stat.size;
      } catch (_) {
        _streamSizeB = 0;
      }
      _streamDate = today;
    } catch (err) {
      console.warn('[ndjson] Failed to open log stream:', err.message);
      return null;
    }
  }

  return _stream;
}

// ---------------------------------------------------------------------------
// Public write API
// ---------------------------------------------------------------------------

/**
 * Write a structured event to the NDJSON log.
 *
 * @param {string} channel  - dot-namespaced source (e.g. 'tiktok:chat', 'router:llm')
 * @param {string} event    - snake_case event name (e.g. 'CHAT_MESSAGE')
 * @param {object} [payload] - arbitrary structured payload
 */
function write(channel, event, payload = {}) {
  _seq += 1;
  const record = {
    ts     : new Date().toISOString(),
    seq    : _seq,
    channel,
    event,
    payload,
  };

  const line = JSON.stringify(record) + '\n';

  const stream = _getStream();
  if (!stream) return;

  try {
    stream.write(line);
    _streamSizeB += Buffer.byteLength(line, 'utf8');
  } catch (err) {
    console.warn('[ndjson] Write error:', err.message);
  }
}

/**
 * Flush and close the current stream.
 * Call on process exit for clean shutdown.
 */
function close() {
  if (_stream) {
    try { _stream.end(); } catch (_) {}
    _stream = null;
  }
}

/**
 * Current log file path (for external log shipping or tailing).
 */
function currentFile() {
  if (!_enabled) return null;
  return _currentFilePath(_streamDate || _dateString());
}

/**
 * Status snapshot.
 */
function status() {
  return {
    enabled    : _enabled,
    dir        : _dir,
    currentFile: currentFile(),
    streamDate : _streamDate,
    streamSizeB: _streamSizeB,
    totalEvents: _seq,
  };
}

module.exports = { configure, write, close, currentFile, status };
