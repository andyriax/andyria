/**
 * core/sleeper.js
 * Sleep mode manager — after SLEEP_AFTER_MS of inactivity, the agent enters
 * sleep mode and begins running the use-case finder on a ticker cycle.
 *
 * Lifecycle:
 *   idle activity  ──10min──►  SLEEPING  ──use-case ticks──►  ...
 *   any TikTok event           ──────────────────────────────►  AWAKE
 *
 * The sleeper is purely additive — it never blocks event handling.
 * tick() is called on each use-case cycle.
 * wake() is called on every real TikTok event to reset the idle timer.
 */

'use strict';

const { appendEvent }    = require('./db');
const { runCycle }       = require('./usecasefinder');

const SLEEP_AFTER_MS  = 10 * 60 * 1000;   // 10 minutes
const TICK_INTERVAL_MS = 2 * 60 * 1000;   // run use-case finder every 2 min while sleeping

let _sleeping    = false;
let _sleepTimer  = null;    // fires once to enter sleep
let _tickTimer   = null;    // repeats while sleeping
let _lastActive  = Date.now();
let _sleepStart  = null;
let _onSleep     = null;    // optional callback: () => void
let _onWake      = null;    // optional callback: () => void
let _speak       = null;    // speak function injected from agent.js

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

function _idleMinutes() {
  return (Date.now() - _lastActive) / 60_000;
}

function _enterSleep() {
  if (_sleeping) return;
  _sleeping   = true;
  _sleepStart = Date.now();

  console.log(`\n[sleeper] 💤 Agent entering SLEEP mode after ${Math.round(_idleMinutes())} min idle`);
  appendEvent('sleeper', 'SLEEP_ENTERED', { idle_minutes: _idleMinutes() });

  if (_onSleep) _onSleep();

  // Start the use-case finder ticker
  _runTick();
  _tickTimer = setInterval(_runTick, TICK_INTERVAL_MS);
}

async function _runTick() {
  if (!_sleeping) return;
  const idle = _idleMinutes();
  try {
    const { lens, finding } = await runCycle(idle);
    console.log(`\n[use-case] ${lens}`);
    console.log(`           ${finding}`);

    // Optionally speak the finding (low-key, single layer)
    if (_speak) {
      _speak(finding, { rate: 0.9 });
    }
  } catch (e) {
    console.warn('[sleeper] use-case cycle error:', e.message);
  }
}

function _scheduleSleepTimer() {
  if (_sleepTimer) clearTimeout(_sleepTimer);
  _sleepTimer = setTimeout(_enterSleep, SLEEP_AFTER_MS);
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Initialise the sleeper. Call once after agent boot.
 * @param {{ speak?: function, onSleep?: function, onWake?: function }} opts
 */
function init(opts = {}) {
  _speak   = opts.speak   || null;
  _onSleep = opts.onSleep || null;
  _onWake  = opts.onWake  || null;
  _lastActive = Date.now();

  _scheduleSleepTimer();
  console.log('[sleeper] Idle watchdog armed — sleep in 10 min if no activity');
}

/**
 * Wake the agent. Must be called on every TikTok event.
 * Resets the idle timer and exits sleep mode if sleeping.
 */
function wake() {
  _lastActive = Date.now();

  if (_sleeping) {
    _sleeping = false;
    if (_tickTimer)  { clearInterval(_tickTimer);  _tickTimer  = null; }

    const sleptMs = Date.now() - (_sleepStart || Date.now());
    console.log(`[sleeper] ⚡ Woke up after ${Math.round(sleptMs / 1000)}s sleep`);
    appendEvent('sleeper', 'SLEEP_EXITED', { slept_ms: sleptMs });

    if (_onWake) _onWake();
  }

  // Re-arm the sleep countdown
  _scheduleSleepTimer();
}

/**
 * Forcibly enter sleep mode immediately (for testing / manual trigger).
 */
function forceSleep() {
  if (_sleepTimer) clearTimeout(_sleepTimer);
  _enterSleep();
}

/**
 * Stop the sleeper entirely (on agent shutdown).
 */
function destroy() {
  if (_sleepTimer) clearTimeout(_sleepTimer);
  if (_tickTimer)  clearInterval(_tickTimer);
  _sleeping   = false;
  _sleepTimer = null;
  _tickTimer  = null;
}

function isSleeping() { return _sleeping; }

function status() {
  return {
    sleeping     : _sleeping,
    idleMinutes  : _idleMinutes(),
    sleepAfterMin: SLEEP_AFTER_MS / 60_000,
    tickIntervalMin: TICK_INTERVAL_MS / 60_000,
  };
}

module.exports = { init, wake, forceSleep, destroy, isSleeping, status };
