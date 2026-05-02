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
const { appendEvent }                      = require('./db');
const llm                                  = require('./llm');

// Per-user conversation history (last 6 turns, in-memory)
const _history = new Map();
function _getHistory(user) { return _history.get(user) || []; }
function _pushHistory(user, role, content) {
  const h = _getHistory(user);
  h.push({ role, content });
  _history.set(user, h.slice(-6));
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
 * Default ambient handler — routes non-command chat through the LLM waterfall.
 * Falls back through 7 providers, always returns a response.
 */
async function _llmAmbient(message, ctx) {
  const user    = ctx.user || 'viewer';
  const history = _getHistory(user);

  try {
    _pushHistory(user, 'user', message);
    const reply = await llm.chat(message, { user, history });
    _pushHistory(user, 'assistant', reply);

    appendEvent(`router:llm`, 'LLM_AMBIENT_REPLY', {
      user,
      provider: llm.getLastProvider(),
      msg_len : message.length,
    });

    ctx.dag?.transition('SPEAKING', { source: 'llm' });
    ctx.speak(reply);
    ctx.dag?.state === 'SPEAKING' && ctx.dag?.transition('IDLE');
  } catch (e) {
    // Should never reach here — llm.chat() has static fallback built in
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
