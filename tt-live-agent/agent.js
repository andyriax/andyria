/**
 * agent.js
 * Jetstreamin TikTok Live Agent — main entrypoint.
 *
 * Usage:
 *   node agent.js                          # default persona, live mode
 *   node agent.js --persona hypegirl       # load specific persona
 *   node agent.js --dry-run                # no TikTok connection, stdin commands
 *   node agent.js --skill reload           # reload skills and exit
 *   node agent.js --promote-capsule        # print sealed capsule identity
 *   node agent.js --dag snapshot           # print DAG states for all agents
 *   node agent.js --revenue stats          # print revenue totals and exit
 *   node agent.js --self-explorer          # start OpenClaw background self-explorer
 *   node agent.js --explore                # one-shot gap discovery (dry-run)
 *   node agent.js --explore --dry-run      # same, no file writes
 */

'use strict';

const path    = require('path');
const fs      = require('fs');
const argv    = require('minimist')(process.argv.slice(2));
const chokidar = require('chokidar');

// ── Core modules ────────────────────────────────────────────────────────────
const { loadOrCreateCapsule, seal } = require('./core/identity');
const { appendEvent, setMemory, revenueTotal } = require('./core/db');
const { loadPersona, get: getPersona, format } = require('./core/persona');
const { loadSkills, listSkills }    = require('./core/skillLoader');
const { route }                     = require('./core/router');
const voice                         = require('./core/voice');
const llm                           = require('./core/llm');
const { loadAgents, dispatch, listAgents } = require('./core/orchestrator');
const { handleGift }                = require('./skills/revenue');
const { DAGStateMachine }           = require('./core/dag');
const sleeper                       = require('./core/sleeper');
const openclaw                      = require('./core/openclaw');

// ── Config ───────────────────────────────────────────────────────────────────
const config = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'config.json'), 'utf8')
);

// Persist config to memory so skills can read it
setMemory('config', config);

// ── Capsule identity ─────────────────────────────────────────────────────────
const capsule = loadOrCreateCapsule();
console.log(`\n╔══════════════════════════════════════════════════════╗`);
console.log(`║  🚀 Jetstreamin TikTok Live Agent  [DAG Capsule]      ║`);
console.log(`║  Capsule: ${capsule.capsule_id.slice(0, 36)}  ║`);
console.log(`╚══════════════════════════════════════════════════════╝\n`);

// ── CLI flags — one-shot commands ────────────────────────────────────────────

if (argv['promote-capsule'] || argv['promote']) {
  console.log(JSON.stringify(capsule, null, 2));
  process.exit(0);
}

if (argv['skill'] === 'reload' || argv['skill'] === 'list') {
  loadSkills();
  if (argv['skill'] === 'list') {
    listSkills().forEach(s => console.log(`  ${s.command.padEnd(18)} ${s.description}`));
  } else {
    console.log('[agent] Skills reloaded.');
  }
  process.exit(0);
}

if (argv['dag'] === 'snapshot') {
  loadAgents(() => {});
  listAgents().forEach(a =>
    console.log(`  ${a.id.padEnd(20)} persona=${a.persona}  state=${a.state}`)
  );
  process.exit(0);
}

if (argv['revenue'] === 'stats') {
  const total = revenueTotal();
  console.log(`[revenue] Total coins: ${total}`);
  process.exit(0);
}

if (argv['explore']) {
  // One-shot: discover gaps, print them, exit (--dry-run skips implementation)
  openclaw.runCycle({ dryRun: argv['dry-run'] !== false }).then(r => {
    if (r.gaps.length === 0) { console.log('No gaps found.'); }
    else r.gaps.forEach(g => console.log(`${g.cmd.padEnd(20)} ${g.reason}`));
    process.exit(0);
  }).catch(e => { console.error(e.message); process.exit(1); });
}

if (argv['sleep'] === 'status') {
  // Instantiate sleeper briefly to print status without starting a live session
  const { status } = require('./core/sleeper');
  console.log(JSON.stringify(status(), null, 2));
  process.exit(0);
}

// ── Boot ─────────────────────────────────────────────────────────────────────

const personaName = argv['persona'] || config.default_persona || 'default';
loadPersona(personaName);
voice.configure(config.voice, getPersona());

loadSkills();
loadAgents((text, opts) => voice.speak(text, opts));
llm.configure(config.llm);

// Single global DAG for the main chat-commander (fallback)
const globalDag = new DAGStateMachine('main');

// Global context object
const ctx = {
  user  : 'viewer',
  dag   : globalDag,
  config,
  speak : (text, opts) => voice.speak(text, opts),
  log   : (type, payload) => appendEvent('agent:main', type, payload),
};

function _emitAssistantSignal(channel, event, payload = {}) {
  try {
    dispatch(channel, {
      uniqueId: payload.user || payload.username || 'system',
      userId: payload.user || payload.username || 'system',
      event,
      ...payload,
    }, ctx);
  } catch (_) {
    // Assistant signals are additive and should never block the live runtime.
  }
}

// ── Sleep / Use-Case Finder ─────────────────────────────────────────────────
sleeper.init({
  speak  : (text, opts) => voice.speak(text, { ...opts }),
  onSleep: () => {
    console.log('[agent] 💤 Sleeping — use-case finder active');
    _emitAssistantSignal('system', 'sleep_entered', { severity: 'info' });
  },
  onWake : () => {
    console.log('[agent] ⚡ Awake — resuming live event handling');
    _emitAssistantSignal('system', 'sleep_exited', { severity: 'info' });
  },
});

// ── OpenClaw self-explorer (background, optional) ─────────────────────────────
if (argv['self-explorer']) {
  const { push } = require('./core/sleepscreen');
  openclaw.setPushFn((type, data) => push(type, data));
  openclaw.start({ dryRun: !!argv['dry-run'] });
  console.log('[agent] 🦅 OpenClaw self-explorer running in background');
}

// ── Hot-reload skills ─────────────────────────────────────────────────────────
const SKILLS_DIR = path.join(__dirname, 'skills');
chokidar.watch(SKILLS_DIR, { ignoreInitial: true }).on('change', file => {
  console.log(`[hot-reload] ${path.basename(file)} changed — reloading skills`);
  loadSkills();
  appendEvent('agent:main', 'HOT_RELOAD', { file });
});

// ── Dry-run mode (stdin commands, no TikTok) ──────────────────────────────────
const dryRun = argv['dry-run'] || config.dry_run || false;

if (dryRun) {
  console.log('[agent] DRY-RUN mode — type /commands below (Ctrl+C to quit)');
  console.log('[agent] Built-ins: /sleep force  /wake  /sleep status\n');
  appendEvent('agent:main', 'AGENT_STARTED', { mode: 'dry-run', persona: personaName });

  const rl = require('readline').createInterface({
    input : process.stdin,
    output: process.stdout,
    prompt: '> ',
  });
  rl.prompt();
  rl.on('line', line => {
    const text = line.trim();
    if (!text) { rl.prompt(); return; }

    // Built-in sleep controls (dry-run only)
    if (text === '/sleep force') { sleeper.forceSleep(); rl.prompt(); return; }
    if (text === '/wake')        { sleeper.wake();       rl.prompt(); return; }
    if (text === '/sleep status') {
      console.log(JSON.stringify(sleeper.status(), null, 2));
      rl.prompt();
      return;
    }

    sleeper.wake(); // any input wakes the agent
    ctx.user = 'local-user';
    globalDag.transition('LISTENING', { source: 'stdin' });
    route(text, ctx);
    globalDag.state === 'LISTENING' && globalDag.transition('IDLE');
    rl.prompt();
  });
  rl.on('close', () => {
    console.log('\n[agent] Session ended.');
    process.exit(0);
  });

  return;   // skip TikTok connection
}

// ── TikTok Live connection ────────────────────────────────────────────────────
let TikTokConn;
try {
  ({ WebcastPushConnection: TikTokConn } = require('tiktok-live-connector'));
} catch (e) {
  console.error('[agent] tiktok-live-connector not installed. Run: npm install');
  process.exit(1);
}

const USERNAME = argv['username'] || config.tiktok_username;
if (!USERNAME || USERNAME === 'YOUR_TIKTOK_USERNAME') {
  console.error('[agent] Set tiktok_username in config.json or pass --username <handle>');
  process.exit(1);
}

const tiktok = new TikTokConn(USERNAME);
appendEvent('agent:main', 'AGENT_STARTED', { mode: 'live', persona: personaName, username: USERNAME });
_emitAssistantSignal('system', 'agent_started', {
  severity: 'info',
  mode: 'live',
  persona: personaName,
  username: USERNAME,
});

tiktok.connect().then(() => {
  console.log(`[agent] Connected to @${USERNAME}`);
  globalDag.transition('LISTENING', { source: 'tiktok' });
  _emitAssistantSignal('host', 'live_connected', { severity: 'info', username: USERNAME });

}).catch(err => {
  console.error('[agent] Connection failed:', err.message);
  appendEvent('agent:main', 'CONNECT_ERROR', { error: err.message });
  _emitAssistantSignal('system', 'connect_error', { severity: 'critical', error: err.message });
  process.exit(1);
});

// ── TikTok event handlers ─────────────────────────────────────────────────────

// ── Wake on any event ───────────────────────────────────────────────────────
function _wake() { sleeper.wake(); }

// Member join → greeter agent
tiktok.on('member', data => {
  _wake();
  const user = data.uniqueId;
  appendEvent('tiktok:member', 'VIEWER_JOINED', { user });
  _emitAssistantSignal('host', 'member_joined', { severity: 'info', user });

  // Dispatch to orchestrated agents first
  const handled = dispatch('member', data, ctx);

  // Fallback: direct greet if no agent picked it up
  if (!handled) {
    const msg = format('Welcome {user}!', { user });
    voice.speak(msg);
  }
});

// Chat → command router
tiktok.on('chat', data => {
  _wake();
  const user = data.uniqueId;
  const msg  = data.comment || '';

  appendEvent('tiktok:chat', 'CHAT_MESSAGE', { user, msg: msg.slice(0, 200) });
  dispatch('chat', data, ctx);

  // Route /commands
  if (msg.startsWith('/')) {
    ctx.user = user;
    globalDag.state === 'IDLE' && globalDag.transition('LISTENING', { user });
    route(msg, { ...ctx, user });
  }
});

// Gift → autonomous revenue agent
tiktok.on('gift', data => {
  _wake();
  appendEvent('tiktok:gift', 'GIFT_EVENT', {
    user  : data.uniqueId,
    gift  : data.giftName,
    coins : data.diamondCount || data.repeatCount || 0,
  });
  _emitAssistantSignal('host', 'gift_received', {
    severity: 'high',
    user: data.uniqueId,
    gift: data.giftName,
    coins: data.diamondCount || data.repeatCount || 0,
  });

  dispatch('gift', data, ctx);
  handleGift(data, { ...ctx, config });
});

// Like
tiktok.on('like', data => {
  _wake();
  appendEvent('tiktok:like', 'LIKE_EVENT', { user: data.uniqueId, count: data.likeCount });
  dispatch('like', data, ctx);
});

// Share
tiktok.on('share', data => {
  _wake();
  appendEvent('tiktok:share', 'SHARE_EVENT', { user: data.uniqueId });
  _emitAssistantSignal('host', 'stream_shared', { severity: 'info', user: data.uniqueId });
  dispatch('share', data, ctx);
  voice.speak(format('{user} just shared the stream! 🔁', { user: data.uniqueId }));
});

// Connection error / disconnect
tiktok.on('error', err => {
  console.error('[tiktok] Error:', err.message);
  appendEvent('agent:main', 'TIKTOK_ERROR', { error: err.message });
  _emitAssistantSignal('system', 'tiktok_error', { severity: 'critical', error: err.message });
  globalDag.transition('ERROR', { error: err.message });
  globalDag.reset();
});

tiktok.on('disconnected', () => {
  console.warn('[agent] Disconnected from TikTok.');
  appendEvent('agent:main', 'DISCONNECTED', {});
  _emitAssistantSignal('host', 'live_disconnected', { severity: 'critical' });
  globalDag.reset();
});

process.on('SIGINT', () => {
  console.log('\n[agent] Shutting down…');
  _emitAssistantSignal('system', 'agent_stopping', { severity: 'info' });
  sleeper.destroy();
  openclaw.stop();
  appendEvent('agent:main', 'AGENT_STOPPED', { uptime_ms: process.uptime() * 1000 });
  process.exit(0);
});
