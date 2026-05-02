/**
 * core/orchestrator.js
 * Multi-agent coordinator — manages a pool of named agents, each with its
 * own DAG state machine, persona, and skill context.
 *
 * Agents can be spawned, paused, resumed, and retired at runtime.
 * The orchestrator routes incoming events to the correct agent(s).
 *
 * Agent definition (from ./agents/*.json):
 * {
 *   "id": "greeter",
 *   "persona": "default",
 *   "triggers": ["member"],          // TikTok event types this agent handles
 *   "skills":   ["/greet", "/hype"],  // allowed skill set
 *   "autonomous": false               // true → auto-executes on trigger
 * }
 */

'use strict';

const fs   = require('fs');
const path = require('path');

const { DAGStateMachine } = require('./dag');
const { loadPersona }     = require('./persona');
const { getSkill }        = require('./skillLoader');
const { appendEvent, upsertAgentState } = require('./db');
const voice               = require('./voice');

const AGENTS_DIR = path.join(__dirname, '..', 'agents');

const _pool = new Map();   // id → { def, dag, ctx }

// ---------------------------------------------------------------------------
// Boot: load all agent definitions
// ---------------------------------------------------------------------------

function loadAgents(speakFn) {
  if (!fs.existsSync(AGENTS_DIR)) {
    console.warn('[orchestrator] No agents/ directory — single-agent mode');
    return;
  }

  const files = fs.readdirSync(AGENTS_DIR).filter(f => f.endsWith('.json'));
  for (const file of files) {
    try {
      const def = JSON.parse(
        fs.readFileSync(path.join(AGENTS_DIR, file), 'utf8')
      );
      _spawnAgent(def, speakFn);
    } catch (e) {
      console.error(`[orchestrator] Failed to load agent ${file}:`, e.message);
    }
  }

  console.log(`[orchestrator] Agents loaded: ${[..._pool.keys()].join(', ')}`);
}

function _spawnAgent(def, speakFn) {
  const dag = new DAGStateMachine(def.id);

  // Load persona for this agent
  try { loadPersona(def.persona || 'default'); } catch (_) { /* use current */ }

  const ctx = {
    agentId: def.id,
    def,
    dag,
    speak: (text, opts) => {
      dag.transition('SPEAKING', { text: text.slice(0, 80) });
      (speakFn || voice.speak)(text, opts);
      dag.transition('IDLE');
    },
    log: (type, payload) => appendEvent(`agent:${def.id}`, type, payload),
    user: null,   // set per-event
  };

  _pool.set(def.id, { def, dag, ctx });
  upsertAgentState(def.id, def.persona || 'default', 1);
  appendEvent(`agent:${def.id}`, 'AGENT_SPAWNED', { persona: def.persona });
}

// ---------------------------------------------------------------------------
// Event routing to agents
// ---------------------------------------------------------------------------

/**
 * Dispatch a TikTok event to all agents that handle its type.
 * @param {string} eventType  - "member" | "chat" | "gift" | "like" | "share"
 * @param {object} data       - raw TikTok event payload
 * @param {object} globalCtx  - global speak/log context
 */
function dispatch(eventType, data, globalCtx) {
  let handled = false;

  for (const [, entry] of _pool) {
    const { def, dag, ctx } = entry;
    if (!def.triggers || !def.triggers.includes(eventType)) continue;
    if (!def.active !== false && dag.state === 'ERROR') continue;

    ctx.user = data.uniqueId || data.userId || 'viewer';
    dag.transition('LISTENING', { event: eventType, user: ctx.user });

    if (def.autonomous && def.skills?.length) {
      // Auto-execute first matched skill
      const skill = getSkill(def.skills[0]);
      if (skill) {
        try {
          dag.transition('ROUTING', { skill: def.skills[0] });
          dag.transition('EXECUTING', { skill: def.skills[0] });
          skill.execute([], { ...ctx, ...globalCtx, data });
          dag.transition('IDLE');
          appendEvent(`agent:${def.id}`, 'AUTO_SKILL_EXECUTED', {
            skill: def.skills[0], user: ctx.user,
          });
        } catch (e) {
          dag.transition('ERROR', { error: e.message });
          dag.reset();
        }
      }
    }

    handled = true;
  }

  return handled;
}

function listAgents() {
  return [..._pool.entries()].map(([id, { def, dag }]) => ({
    id,
    persona : def.persona,
    state   : dag.state,
    triggers: def.triggers,
    active  : def.active !== false,
  }));
}

function getAgent(id) {
  return _pool.get(id) || null;
}

module.exports = { loadAgents, dispatch, listAgents, getAgent };
