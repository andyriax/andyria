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
const { loadPersona, listPersonas, get: getPersona, updateCurrentPersona, saveCurrentPersona } = require('./persona');
const { appendEvent, setMemory, getMemory } = require('./db');
const llm                                  = require('./llm');
const conversational                       = require('./conversational');

const ROLE_RANK = {
  viewer: 1,
  mod: 2,
  admin: 3,
};

const DEFAULT_COMMAND_PERMS = {
  '/help': 'viewer',
  '/stats': 'viewer',
  '/history': 'viewer',
  '/reset': 'mod',
  '/shoutout': 'mod',
  '/game': 'viewer',
  '/revenue': 'viewer',
  '/drop': 'mod',
  '/persona': 'admin',
  '/skill': 'admin',
  '/dag': 'admin',
  '/sleep': 'mod',
  '/wake': 'mod',
  '/conversational:on': 'mod',
  '/conversational:off': 'mod',
  '/conversational:toggle': 'mod',
  '/conversational:status': 'viewer',
  '/wakeword': 'mod',
};

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

function _requiredRoleFor(cmd, ctx) {
  const fromCtx = ctx?.commandPermissions || {};
  return fromCtx[cmd] || DEFAULT_COMMAND_PERMS[cmd] || 'viewer';
}

function _hasRole(userRole, requiredRole) {
  const current = ROLE_RANK[userRole] || ROLE_RANK.viewer;
  const needed  = ROLE_RANK[requiredRole] || ROLE_RANK.viewer;
  return current >= needed;
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
async function route(message, ctx) {
  const trimmed = (message || '').trim();

  // Not a command — LLM ambient handler (with full provider waterfall)
  if (!trimmed.startsWith('/')) {
    const handler = _ambientHandler || _llmAmbient;
    await handler(trimmed, ctx);
    return { handled: true, kind: 'ambient' };
  }

  const parts = trimmed.split(/\s+/);
  const cmd   = parts[0].toLowerCase();
  const args  = parts.slice(1);
  const role  = ctx?.role || 'viewer';

  appendEvent(`router:${ctx.user}`, 'COMMAND_RECEIVED', { cmd, args, user: ctx.user });

  const requiredRole = _requiredRoleFor(cmd, ctx);
  if (!_hasRole(role, requiredRole)) {
    const msg = `@${ctx.user} ${cmd} requires ${requiredRole} role.`;
    ctx.speak(msg);
    appendEvent('router:auth', 'COMMAND_DENIED', {
      cmd,
      user: ctx.user,
      role,
      requiredRole,
    });
    return { handled: true, kind: 'denied', requiredRole };
  }

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
    } else if (sub === 'status') {
      const p = getPersona();
      ctx.speak(`Persona: ${p?.name || 'default'} | mission: ${p?.mission || 'n/a'} | prefix: ${p?.style?.prefix || '-'} | suffix: ${p?.style?.suffix || '-'}`);
    } else if (sub === 'set') {
      const field = String(args[1] || '').toLowerCase();
      const value = args.slice(2).join(' ').trim();
      if (!field || !value) {
        ctx.speak('Usage: /persona set mission|prefix|suffix|name <value>');
        return { handled: true, kind: 'builtin' };
      }
      if (field === 'mission') {
        updateCurrentPersona({ mission: value });
      } else if (field === 'prefix') {
        updateCurrentPersona({ style: { prefix: value } });
      } else if (field === 'suffix') {
        updateCurrentPersona({ style: { suffix: value } });
      } else if (field === 'name') {
        updateCurrentPersona({ name: value });
      } else {
        ctx.speak('Supported fields: mission, prefix, suffix, name');
        return { handled: true, kind: 'builtin' };
      }
      ctx.speak(`Persona ${field} updated.`);
      appendEvent('router:builtin', 'PERSONA_UPDATED', { field, value, user: ctx.user });
    } else if (sub === 'save') {
      const target = args[1];
      if (!target) {
        ctx.speak('Usage: /persona save <name>');
        return { handled: true, kind: 'builtin' };
      }
      try {
        const id = saveCurrentPersona(target);
        ctx.speak(`Persona saved as ${id}.`);
      } catch (e) {
        ctx.speak(`Failed to save persona: ${e.message}`);
      }
    } else {
      ctx.speak(`Available personas: ${listPersonas().join(', ')}. Commands: /persona switch <name> | /persona status | /persona set mission|prefix|suffix|name <value> | /persona save <name>`);
    }
    return { handled: true, kind: 'builtin' };
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
    return { handled: true, kind: 'builtin' };
  }

  if (cmd === '/dag') {
    if (ctx.dag) {
      const snap = ctx.dag.snapshot();
      ctx.speak(`DAG [${snap.agentId}] state: ${snap.state}`);
    }
    return { handled: true, kind: 'builtin' };
  }

  if (cmd === '/reset') {
    const target = args[0] || ctx.user || 'viewer';
    clearHistory(target);
    ctx.speak(`Conversation history cleared for ${target}.`);
    appendEvent('router:builtin', 'HISTORY_RESET', { target });
    return { handled: true, kind: 'builtin' };
  }

  if (cmd === '/history') {
    const target = args[0] || ctx.user || 'viewer';
    const h = _getHistory(target);
    ctx.speak(`${target} has ${h.length} turns in conversation history.`);
    return { handled: true, kind: 'builtin' };
  }

  if (cmd === '/help') {
    const skills = listSkills();
    const builtIns = [
      '/help — list commands',
      '/stats — stream metrics',
      '/history — conversation turns',
      '/game — mini interaction prompt',
    ];
    const lines  = [
      ...builtIns,
      ...skills.map(s => `${s.command}${s.description ? ' — ' + s.description : ''}`),
    ];
    ctx.speak(lines.length ? lines.join('\n') : 'No skills loaded.');
    return { handled: true, kind: 'builtin' };
  }

  if (cmd === '/stats') {
    if (ctx.metrics?.snapshot) {
      const m   = ctx.metrics.snapshot();
      const cs  = conversational.status();
      const upMin = Math.round(m.uptimeMs / 60_000);
      const lines = [
        `Uptime ${upMin}min | Events ${m.events} | Responses ${m.responses} | Errors ${m.errors} | Dropped ${m.dropped}`,
        `Latency avg ${Math.round(m.avgProcessLatencyMs)}ms | Queue avg ${Math.round(m.avgQueueLatencyMs)}ms`,
        `Commands ${m.commandHits} | Fast-path ${m.fastPathHits} | Conversational ${m.conversationalHits} (${Math.round(m.conversationalHitRate * 100)}% of chat) | Rate-limited ${m.convRateLimited}`,
        `Conversational mode: ${cs.enabled ? 'ON' : 'OFF'} | User rate-limit ${cs.rateLimitMs}ms | Tracked users ${cs.trackedUsers}`,
      ];
      ctx.speak(lines.join('\n'));
    } else {
      ctx.speak('Metrics unavailable.');
    }
    return { handled: true, kind: 'builtin' };
  }

  // /conversational:on | /conversational:off | /conversational:toggle | /conversational:status
  if (cmd.startsWith('/conversational:')) {
    const sub     = cmd.slice('/conversational:'.length).toLowerCase();
    const invoker = ctx.user || 'viewer';
    let newState;
    switch (sub) {
      case 'on':
        newState = conversational.enable(invoker);
        ctx.speak(`Conversational mode is now ON. I will respond to all chat messages.`);
        break;
      case 'off':
        newState = conversational.disable(invoker);
        ctx.speak(`Conversational mode is now OFF. Responding to commands only.`);
        break;
      case 'toggle':
        newState = conversational.toggle(invoker);
        ctx.speak(`Conversational mode toggled ${newState ? 'ON' : 'OFF'}.`);
        break;
      case 'status':
      default: {
        const s = conversational.status();
        ctx.speak(`Conversational mode: ${s.enabled ? 'ON' : 'OFF'}. Rate-limit ${s.rateLimitMs}ms per user.`);
        newState = s.enabled;
      }
    }
    appendEvent('router:builtin', 'CONVERSATIONAL_MODE_CHANGED', {
      sub,
      newState,
      invoker,
    });
    return { handled: true, kind: 'builtin' };
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
    return { handled: true, kind: 'builtin' };
  }

  if (cmd === '/wake') {
    const sleeper = require('./sleeper');
    sleeper.wake();
    ctx.speak('Agent is awake.');
    return { handled: true, kind: 'builtin' };
  }

  if (cmd === '/wakeword') {
    const sub = String(args[0] || 'status').toLowerCase();
    const ww = ctx.wakeWord;
    if (!ww || typeof ww.status !== 'function') {
      ctx.speak('Wake-word controls are unavailable in this runtime.');
      return { handled: true, kind: 'builtin' };
    }

    if (sub === 'status') {
      const s = ww.status();
      ctx.speak(`Wake word: ${s.enabled ? 'ON' : 'OFF'} | value: ${s.value} | prefix-only: ${s.requirePrefix ? 'ON' : 'OFF'} | ack-only: ${s.ackOnWakeOnly ? 'ON' : 'OFF'}`);
      return { handled: true, kind: 'builtin' };
    }

    if (sub === 'set') {
      const next = args.slice(1).join(' ').trim();
      if (!next) {
        ctx.speak('Usage: /wakeword set <word or phrase>');
        return { handled: true, kind: 'builtin' };
      }
      const ok = ww.setValue(next, ctx.user || 'viewer');
      if (!ok) {
        ctx.speak('Wake word cannot be empty.');
      } else {
        ctx.speak(`Wake word updated to "${next}".`);
      }
      return { handled: true, kind: 'builtin' };
    }

    if (sub === 'on' || sub === 'off') {
      const enabled = ww.setEnabled(sub === 'on', ctx.user || 'viewer');
      ctx.speak(`Wake-word gate is now ${enabled ? 'ON' : 'OFF'}.`);
      return { handled: true, kind: 'builtin' };
    }

    if (sub === 'prefix') {
      const value = String(args[1] || '').toLowerCase();
      if (value !== 'on' && value !== 'off') {
        ctx.speak('Usage: /wakeword prefix on|off');
        return { handled: true, kind: 'builtin' };
      }
      const next = ww.setRequirePrefix(value === 'on', ctx.user || 'viewer');
      ctx.speak(`Wake-word prefix mode is now ${next ? 'ON' : 'OFF'}.`);
      return { handled: true, kind: 'builtin' };
    }

    if (sub === 'ack') {
      const value = String(args[1] || '').toLowerCase();
      if (value !== 'on' && value !== 'off') {
        ctx.speak('Usage: /wakeword ack on|off');
        return { handled: true, kind: 'builtin' };
      }
      const next = ww.setAckOnWakeOnly(value === 'on', ctx.user || 'viewer');
      ctx.speak(`Wake-word ack-only mode is now ${next ? 'ON' : 'OFF'}.`);
      return { handled: true, kind: 'builtin' };
    }

    ctx.speak('Wake-word commands: /wakeword status | /wakeword set <word> | /wakeword on|off | /wakeword prefix on|off | /wakeword ack on|off');
    return { handled: true, kind: 'builtin' };
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
      return { handled: true, kind: 'skill', cmd };
    } catch (e) {
      console.error(`[router] Skill error (${cmd}):`, e.message);
      ctx.dag?.transition('ERROR', { error: e.message });
      ctx.dag?.reset();
      appendEvent(`skill:${cmd}`, 'SKILL_ERROR', { cmd, error: e.message });
      return { handled: true, kind: 'skill-error', error: e.message };
    }
  }

  console.log(`[router] Unknown command: ${cmd}`);
  return { handled: false, kind: 'unknown', cmd };
}

module.exports = { route, setAmbientHandler };
