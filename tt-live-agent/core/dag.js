/**
 * core/dag.js
 * DAG state machine for the Jetstreamin capsule.
 *
 * Each node in the DAG is a named state with allowed transitions.
 * The agent advances through states as events arrive; the DAG
 * prevents illegal state transitions and provides a replay log.
 *
 * States:
 *   IDLE → LISTENING → ROUTING → EXECUTING → SPEAKING → IDLE
 *   Any state → ERROR → IDLE (recovery)
 */

'use strict';

const { appendEvent } = require('./db');
const { seal }        = require('./identity');

const TRANSITIONS = {
  IDLE      : ['LISTENING', 'ERROR'],
  LISTENING : ['ROUTING', 'IDLE', 'ERROR'],
  ROUTING   : ['EXECUTING', 'IDLE', 'ERROR'],
  EXECUTING : ['SPEAKING', 'IDLE', 'ERROR'],
  SPEAKING  : ['IDLE', 'ERROR'],
  ERROR     : ['IDLE'],
};

class DAGStateMachine {
  constructor(agentId) {
    this.agentId  = agentId;
    this.state    = 'IDLE';
    this.history  = [];           // last 50 transitions
    this._listeners = {};
  }

  /**
   * Attempt a state transition. Returns true on success.
   * @param {string} nextState
   * @param {object} [meta]
   */
  transition(nextState, meta = {}) {
    const allowed = TRANSITIONS[this.state] || [];
    if (!allowed.includes(nextState)) {
      const err = `[dag:${this.agentId}] Illegal transition ${this.state} → ${nextState}`;
      console.warn(err);
      appendEvent(`dag:${this.agentId}`, 'DAG_ILLEGAL_TRANSITION', {
        from: this.state, to: nextState, ...meta,
      });
      return false;
    }

    const prev    = this.state;
    this.state    = nextState;
    const entry   = seal({ agent: this.agentId, from: prev, to: nextState, ...meta });
    this.history  = [entry, ...this.history].slice(0, 50);

    appendEvent(`dag:${this.agentId}`, 'DAG_TRANSITION', {
      from: prev, to: nextState, ...meta,
    });

    this._emit(nextState, entry);
    return true;
  }

  on(state, fn) {
    if (!this._listeners[state]) this._listeners[state] = [];
    this._listeners[state].push(fn);
    return this;
  }

  _emit(state, entry) {
    (this._listeners[state] || []).forEach(fn => {
      try { fn(entry); } catch (e) { console.error('[dag] listener error:', e.message); }
    });
  }

  reset() {
    this.state = 'IDLE';
    appendEvent(`dag:${this.agentId}`, 'DAG_RESET', {});
  }

  snapshot() {
    return { agentId: this.agentId, state: this.state, history: this.history.slice(0, 10) };
  }
}

module.exports = { DAGStateMachine };
