/**
 * core/db.js
 * SQLite CQRS runtime — append-only event log + read-model projections.
 *
 * Commands write events; queries read projections.
 * The event log is the source of truth; projections are derived and
 * can always be rebuilt from the log.
 */

'use strict';

const Database = require('better-sqlite3');
const path     = require('path');
const fs       = require('fs');
const { seal } = require('./identity');

const DATA_DIR = path.join(__dirname, '..', 'data');
if (!fs.existsSync(DATA_DIR)) fs.mkdirSync(DATA_DIR, { recursive: true });

const db = new Database(path.join(DATA_DIR, 'runtime.db'));

// WAL mode for concurrent reads without blocking writes
db.pragma('journal_mode = WAL');
db.pragma('synchronous = NORMAL');

// ---------------------------------------------------------------------------
// Schema
// ---------------------------------------------------------------------------

db.exec(`
  -- Append-only event log (CQRS command side)
  CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    stream_id  TEXT    NOT NULL,
    event_type TEXT    NOT NULL,
    payload    TEXT    NOT NULL,    -- JSON
    seal       TEXT    NOT NULL,    -- SHA3-512 of payload+ts
    ts         INTEGER NOT NULL
  );

  -- Read model: memory key/value projection
  CREATE TABLE IF NOT EXISTS memory (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at INTEGER
  );

  -- Read model: agent state projection
  CREATE TABLE IF NOT EXISTS agent_state (
    agent_id   TEXT PRIMARY KEY,
    persona    TEXT,
    active     INTEGER DEFAULT 1,
    updated_at INTEGER
  );

  -- Read model: revenue ledger
  CREATE TABLE IF NOT EXISTS revenue (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user       TEXT,
    event_type TEXT,
    coins      INTEGER DEFAULT 0,
    ts         INTEGER
  );

  CREATE INDEX IF NOT EXISTS idx_events_stream ON events(stream_id);
  CREATE INDEX IF NOT EXISTS idx_events_type   ON events(event_type);
  CREATE INDEX IF NOT EXISTS idx_events_ts     ON events(ts);
`);

// ---------------------------------------------------------------------------
// CQRS helpers
// ---------------------------------------------------------------------------

const _appendEvent = db.prepare(`
  INSERT INTO events (stream_id, event_type, payload, seal, ts)
  VALUES (@stream_id, @event_type, @payload, @seal, @ts)
`);

/**
 * Append a sealed event to the log.
 * @param {string} streamId  - e.g. "agent:greeter" or "skill:greet"
 * @param {string} eventType - e.g. "SKILL_EXECUTED" | "GIFT_RECEIVED"
 * @param {object} payload
 * @returns {object} sealed event row
 */
function appendEvent(streamId, eventType, payload) {
  const ts     = Date.now();
  const sealed = seal({ stream_id: streamId, event_type: eventType, payload, ts });
  _appendEvent.run({
    stream_id  : streamId,
    event_type : eventType,
    payload    : JSON.stringify(payload),
    seal       : sealed._seal,
    ts,
  });
  return sealed;
}

/**
 * Query events by stream or type.
 * @param {{ stream_id?: string, event_type?: string, limit?: number }} opts
 */
function queryEvents({ stream_id, event_type, limit = 100 } = {}) {
  let sql = 'SELECT * FROM events';
  const params = [];
  const where  = [];

  if (stream_id)  { where.push('stream_id = ?');  params.push(stream_id); }
  if (event_type) { where.push('event_type = ?'); params.push(event_type); }
  if (where.length) sql += ' WHERE ' + where.join(' AND ');
  sql += ' ORDER BY ts DESC LIMIT ?';
  params.push(limit);

  return db.prepare(sql).all(...params).map(row => ({
    ...row,
    payload: JSON.parse(row.payload),
  }));
}

// Memory projection helpers
const _setMem = db.prepare(
  `INSERT INTO memory (key, value, updated_at) VALUES (?, ?, ?)
   ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at`
);
const _getMem = db.prepare(`SELECT value FROM memory WHERE key = ?`);

function setMemory(key, value) {
  _setMem.run(key, typeof value === 'string' ? value : JSON.stringify(value), Date.now());
}

function getMemory(key, defaultVal = null) {
  const row = _getMem.get(key);
  if (!row) return defaultVal;
  try { return JSON.parse(row.value); } catch (_) { return row.value; }
}

// Agent state projection
const _upsertAgent = db.prepare(`
  INSERT INTO agent_state (agent_id, persona, active, updated_at)
  VALUES (@agent_id, @persona, @active, @updated_at)
  ON CONFLICT(agent_id) DO UPDATE SET
    persona=excluded.persona,
    active=excluded.active,
    updated_at=excluded.updated_at
`);

function upsertAgentState(agentId, persona, active = 1) {
  _upsertAgent.run({ agent_id: agentId, persona, active, updated_at: Date.now() });
}

function getAgentState(agentId) {
  return db.prepare(`SELECT * FROM agent_state WHERE agent_id = ?`).get(agentId) || null;
}

// Revenue ledger
const _logRevenue = db.prepare(
  `INSERT INTO revenue (user, event_type, coins, ts) VALUES (?, ?, ?, ?)`
);

function logRevenue(user, eventType, coins) {
  _logRevenue.run(user, eventType, coins, Date.now());
  appendEvent(`revenue:${user}`, 'REVENUE_EVENT', { user, eventType, coins });
}

function revenueTotal() {
  return (db.prepare(`SELECT SUM(coins) as total FROM revenue`).get() || {}).total || 0;
}

module.exports = {
  db,
  appendEvent,
  queryEvents,
  setMemory,
  getMemory,
  upsertAgentState,
  getAgentState,
  logRevenue,
  revenueTotal,
};
