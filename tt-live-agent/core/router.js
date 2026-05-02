/**
 * core/router.js
 * Command router — dispatches /command messages to skill handlers.
 * Non-command messages are forwarded to the ambient handler if one is set.
 *
 * Supports:
 *   /command arg1 arg2         → exact skill lookup
 *   /persona switch <name>     → built-in persona switch
 *   /skill reload              → built-in hot-reload
 *   /skill list                → list all loaded skills
 *   /dag snapshot              → dump DAG state
 */

'use strict';

const { getSkill, loadSkills, listSkills } = require('./skillLoader');
const { loadPersona, listPersonas }        = require('./persona');
const { appendEvent, setMemory, getMemory } = require('./db');
const llm                                  = require('./llm');

// ---------------------------------------------------------------------------
// Per-user conversation history — DB-persisted (survives restarts)
// In-memory write-through cache on top of SQLite memory table
// ---------------------------------------------------------------------------
const _historyCache = new Map();

const MAX_HISTORY = 20; // configurable: last 20 turns (10 exchanges)

function _historyKey(user) { return `conv:${user}`; }

function _getHistory(user) {
  if (_historyCache.has(user)) return _historyCache.get(user);
  try {
    const raw = getMemory(_historyKey(user));
    const h   = raw ? JSON.parse(raw) : [];
    _historyCache.set(user, h);
    return h;
  } catch (_) {
    return [];
  }
}

function _pushHistory(user, role, content) {
  const h = _getHistory(user);
  h.push({ role, content });
  const trimmed = h.slice(-MAX_HISTORY);
  _historyCache.set(user, trimmed);
  try { setMemory(_historyKey(user), JSON.stringify(trimmed)); } catch (_) {}
}

/**
 * Clear a user's conversation history (e.g. on /reset).
 */
function clearHistory(user) {
  _historyCache.set(user, []);
  try { setMemory(_historyKey(user), '[]'); } catch (_) {}
}

let _ambientHandler = null;

/**
 * Register a catch-all for non-command messages.
 * Overrides the default LLM ambient handler.
 * @param {function} fn  (message, ctx) => void
 */
function setAmbientHandler(fn) {
  _ambientHandler = fn;
}

/**
 * Default ambient handler — full conversationalist mode.
 * Builds a complete multi-turn messages array and sends through llm.thread().
 * Every exchange is persisted to SQLite so context survives restarts.
 */
async function _llmAmbient(message, ctx) {
  const user = ctx.user || 'viewer';

  // Append user message to persistent history
  _pushHistory(user, 'user', message);
  const history = _getHistory(user);

  // Build fully-constructed messages array for multi-turn context
  const messages = llm.buildMessages(message, {
    history        : history.slice(0, -1), // all prior turns (user msg already in history)
    maxHistory     : MAX_HISTORY,
    systemPrompt   : ctx.config?.llm?.system_prompt,
  });

  // Replace last user entry to avoid double-adding the current message
  // (buildMessages appends it; history already has it)
  // Actually: buildMessages adds userMessage as final entry — that's correct.
  // We pass history WITHOUT the current user message (slice(0,-1)) + let buildMessages append it.

  try {
    ctx.dag?.transition('ROUTING', { source: 'ambient' });
    const reply = await llm.thread(messages, { user });
    _pushHistory(user, 'assistant', reply);

    appendEvent('router:llm', 'LLM_AMBIENT_REPLY', {
      user,
      provider  : llm.getLastProvider(),
      turns     : history.length,
      msg_len   : message.length,
    });

    ctx.dag?.transition('SPEAKING', { source: 'llm' });
    ctx.speak(reply);
    ctx.dag?.state === 'SPEAKING' && ctx.dag?.transition('IDLE');
  } catch (e) {
    // Should never reach here — llm.thread() has static fallback built in
    console.error('[router:llm] Unexpected error:', e.message);
  }
}

/**
 * Route an incoming message.
 * @param {string} message
 * @param {object} ctx  - { user, speak, log, dag, config }
 */
function route(message, ctx) {
  const trimmed = (message || '').trim();

  // Not a command — LLM ambient handler (with full provider waterfall)
  if (!trimmed.startsWith('/')) {
    const handler = _ambientHandler || _llmAmbient;
    handler(trimmed, ctx);
    return;
  }

  const parts = trimmed.split(/\s+/);
  const cmd   = parts[0].toLowerCase();
  const args  = parts.slice(1);

  appendEvent(`router:${ctx.user}`, 'COMMAND_RECEIVED', { cmd, args, user: ctx.user });

  // ── Built-in commands ──────────────────────────────────────────────────

  if (cmd === '/persona') {
    const sub  = args[0];
    const name = args[1];
    if (sub === 'switch' && name) {
      try {
        loadPersona(name);
        ctx.speak(`Persona switched to ${name}`);
        appendEvent('router:builtin', 'PERSONA_SWITCHED', { name });
      } catch (e) {
        ctx.speak(`Unknown persona: ${name}. Available: ${listPersonas().join(', ')}`);
      }
    } else {
      ctx.speak(`Available personas: ${listPersonas().join(', ')}`);
    }
    return;
  }

  if (cmd === '/skill') {
    const sub = args[0];
    if (sub === 'reload') {
      loadSkills();
      ctx.speak('Skills reloaded.');
    } else if (sub === 'list' || !sub) {
      const skills = listSkills();
      ctx.speak(`Loaded skills: ${skills.map(s => s.command).join(', ') || 'none'}`);
    }
    return;
  }

  if (cmd === '/dag') {
    if (ctx.dag) {
      const snap = ctx.dag.snapshot();
      ctx.speak(`DAG [${snap.agentId}] state: ${snap.state}`);
    }
    return;
  }

  if (cmd === '/reset') {
    const target = args[0] || ctx.user || 'viewer';
    clearHistory(target);
    ctx.speak(`Conversation history cleared for ${target}.`);
    appendEvent('router:builtin', 'HISTORY_RESET', { target });
    return;
  }

  if (cmd === '/history') {
    const target = args[0] || ctx.user || 'viewer';
    const h = _getHistory(target);
    ctx.speak(`${target} has ${h.length} turns in conversation history.`);
    return;
  }

  if (cmd === '/help') {
    const skills = listSkills();
    const lines  = skills.map(s => `${s.command}${s.description ? ' — ' + s.description : ''}`);
    ctx.speak(lines.length ? lines.join('\n') : 'No skills loaded.');
    return;
  }

  if (cmd === '/sleep') {
    const sub = args[0];
    const sleeper = require('./sleeper');
    if (sub === 'force') {
      sleeper.forceSleep();
      ctx.speak('Entering sleep mode — use-case finder active.');
    } else {
      const s = sleeper.status();
      ctx.speak(`Sleep: ${s.sleeping ? 'SLEEPING' : 'AWAKE'}, idle ${Math.round(s.idleMinutes)}min`);
    }
    return;
  }

  if (cmd === '/wake') {
    const sleeper = require('./sleeper');
    sleeper.wake();
    ctx.speak('Agent is awake.');
    return;
  }

  // ── Skill dispatch ─────────────────────────────────────────────────────

  const skill = getSkill(cmd);
  if (skill) {
    try {
      ctx.dag?.transition('ROUTING', { cmd });
      ctx.dag?.transition('EXECUTING', { cmd });
      skill.execute(args, ctx);
      appendEvent(`skill:${cmd}`, 'SKILL_EXECUTED', { cmd, args, user: ctx.user });
      ctx.dag?.transition('IDLE');
    } catch (e) {
      console.error(`[router] Skill error (${cmd}):`, e.message);
      ctx.dag?.transition('ERROR', { error: e.message });
      ctx.dag?.reset();
      appendEvent(`skill:${cmd}`, 'SKILL_ERROR', { cmd, error: e.message });
    }
    return;
  }

  console.log(`[router] Unknown command: ${cmd}`);
}

module.exports = { route, setAmbientHandler };
