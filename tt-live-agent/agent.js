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
const { loadOrCreateCapsule } = require('./core/identity');
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
const { AsyncPriorityQueue }        = require('./core/priorityQueue');
const { LiveState }                 = require('./core/liveState');
const { Metrics }                   = require('./core/metrics');
const { classifyIntent, responseTemplate } = require('./core/intent');
const conversational                = require('./core/conversational');
const ndjson                        = require('./core/ndjson');

// ── Config ───────────────────────────────────────────────────────────────────
const config = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'config.json'), 'utf8')
);

// Persist config to memory so skills can read it
setMemory('config', config);

// ── NDJSON logger ─────────────────────────────────────────────────────────
ndjson.configure(config.logging?.ndjson || {});

// ── Conversational mode ───────────────────────────────────────────────────
conversational.configure(config.conversational || {});

// CLI override: --conversational-on / --conversational-off
if (argv['conversational-on'])  conversational.enable('cli');
if (argv['conversational-off']) conversational.disable('cli');

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
const eventQueue = new AsyncPriorityQueue();
const state = new LiveState(config.engagement || {});
const metrics = new Metrics();

function speakAndLog(text, opts = {}, meta = {}) {
  console.log(`[speak] ${text}`);
  const payload = { text: String(text || '').slice(0, 280), ...meta };
  appendEvent('agent:voice', 'VOICE_OUT', payload);
  ndjson.write('agent:voice', 'VOICE_OUT', payload);
  metrics.incResponse();
  voice.speak(text, opts);
}

// Global context object
const ctx = {
  user  : 'viewer',
  role  : 'viewer',
  dag   : globalDag,
  config,
  metrics,
  commandPermissions: config.commands?.permissions || {},
  speak : (text, opts) => speakAndLog(text, opts),
  log   : (type, payload) => appendEvent('agent:main', type, payload),
};

// ── Sleep / Use-Case Finder ─────────────────────────────────────────────────
sleeper.init({
  speak  : (text, opts) => speakAndLog(text, { ...opts }, { source: 'sleeper' }),
  onSleep: () => { console.log('[agent] 💤 Sleeping — use-case finder active'); },
  onWake : () => { console.log('[agent] ⚡ Awake — resuming live event handling'); },
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
  ndjson.write('agent:main', 'AGENT_STARTED', { mode: 'dry-run', persona: personaName, conversational: conversational.status() });

  const rl = require('readline').createInterface({
    input : process.stdin,
    output: process.stdout,
    prompt: '> ',
  });
  rl.prompt();
  rl.on('line', async line => {
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
    ctx.role = 'admin';
    globalDag.transition('LISTENING', { source: 'stdin' });
    await route(text, ctx);
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

const priorities = {
  gift: config.runtime?.queuePriorities?.gift ?? 100,
  command: config.runtime?.queuePriorities?.command ?? 80,
  member: config.runtime?.queuePriorities?.member ?? 60,
  follow: config.runtime?.queuePriorities?.follow ?? 50,
  share: config.runtime?.queuePriorities?.share ?? 40,
  like: config.runtime?.queuePriorities?.like ?? 35,
  chat: config.runtime?.queuePriorities?.chat ?? 20,
};

const reconnectCfg = {
  enabled: config.runtime?.reconnect?.enabled !== false,
  baseDelayMs: config.runtime?.reconnect?.baseDelayMs || 1500,
  maxDelayMs: config.runtime?.reconnect?.maxDelayMs || 30000,
};

let tiktok = null;
let reconnectAttempt = 0;
let reconnectTimer = null;
let shuttingDown = false;

function resolveRole(data) {
  if (data?.isModerator || data?.mod || data?.userDetails?.isModerator) return 'mod';
  if (data?.isHost || data?.owner || data?.uniqueId === USERNAME) return 'admin';
  return 'viewer';
}

function enqueueEvent(type, data, basePriority) {
  eventQueue.enqueue({
    type,
    data,
    enqueuedAt: Date.now(),
  }, basePriority);
}

async function processEvent(ev) {
  const started = Date.now();
  const queueLatency = started - ev.enqueuedAt;
  metrics.observeQueueLatency(queueLatency);
  metrics.incEvent(ev.type);

  const data = ev.data || {};
  const user = data.uniqueId || data.userId || 'viewer';
  const role = resolveRole(data);

  state.upsertUser(user, { role });
  ctx.user = user;
  ctx.role = state.getRole(user);

  globalDag.state === 'IDLE' && globalDag.transition('LISTENING', { source: 'queue', event: ev.type, user });
  appendEvent('trace:queue', 'EVENT_DEQUEUED', { type: ev.type, user, role: ctx.role, queue_latency_ms: queueLatency });
  ndjson.write('trace:queue', 'EVENT_DEQUEUED', { type: ev.type, user, role: ctx.role, queue_latency_ms: queueLatency });

  if (ev.type === 'member') {
    appendEvent('tiktok:member', 'VIEWER_JOINED', { user });
    const userSummary = state.getUserSummary(user);
    const shouldGreet = state.shouldGreet(user);
    const isPriorityUser = userSummary.visits >= (config.engagement?.returningPriorityVisits || 2)
      || userSummary.totalCoins >= (config.engagement?.highValueCoinsThreshold || 200);

    const handled = dispatch('member', data, ctx);
    if (!handled && shouldGreet) {
      const template = isPriorityUser
        ? 'Welcome back {user}, VIP energy in the room.'
        : 'Welcome {user}! Glad you joined the stream!';
      speakAndLog(format(template, { user }), {}, { user, source: 'member-greeting', priority: isPriorityUser });
      state.markGreeted(user);
    }
  }

  if (ev.type === 'gift') {
    const coins = data.diamondCount || data.repeatCount || 0;
    state.recordGift(user, coins);
    appendEvent('tiktok:gift', 'GIFT_EVENT', {
      user,
      gift: data.giftName,
      coins,
    });
    dispatch('gift', data, ctx);
    handleGift(data, { ...ctx, config, speak: speakAndLog });
  }

  if (ev.type === 'like') {
    appendEvent('tiktok:like', 'LIKE_EVENT', { user, count: data.likeCount });
    dispatch('like', data, ctx);
  }

  if (ev.type === 'share') {
    appendEvent('tiktok:share', 'SHARE_EVENT', { user });
    dispatch('share', data, ctx);
    speakAndLog(format('{user} just shared the stream! 🔁', { user }), {}, { source: 'share' });
  }

  if (ev.type === 'follow') {
    appendEvent('tiktok:follow', 'FOLLOW_EVENT', { user });
    speakAndLog(format('Big follow from {user}, welcome to the squad.', { user }), {}, { source: 'follow' });
  }

  if (ev.type === 'chat' || ev.type === 'command') {
    const msg = String(data.comment || '').trim();
    if (!msg) return;

    const spam = state.recordMessage(user, msg, role);
    if (spam.isSpam) {
      metrics.incDropped();
      appendEvent('tiktok:chat', 'CHAT_DROPPED_SPAM', {
        user,
        msg: msg.slice(0, 160),
        rate: spam.messageRate,
        duplicateCount: spam.duplicateCount,
      });
      return;
    }

    appendEvent('tiktok:chat', 'CHAT_MESSAGE', { user, msg: msg.slice(0, 200) });
    ndjson.write('tiktok:chat', 'CHAT_MESSAGE', { user, role, msg: msg.slice(0, 200) });
    dispatch('chat', data, ctx);

    appendEvent('trace:chat', 'CHAT_PARSE_START', { user, msg: msg.slice(0, 200) });
    ndjson.write('trace:chat', 'CHAT_PARSE_START', { user, msg: msg.slice(0, 200) });

    if (msg.startsWith('/')) {
      // ── Slash command ────────────────────────────────────────────────────
      metrics.incCommand();
      globalDag.state === 'IDLE' && globalDag.transition('LISTENING', { user });
      const result = await route(msg, {
        ...ctx,
        user,
        role: ctx.role,
        metrics,
        commandPermissions: config.commands?.permissions || {},
      });
      ndjson.write('trace:chat', 'CHAT_COMMAND_RESULT', { user, cmd: msg.split(/\s+/)[0].toLowerCase(), result });
      appendEvent('trace:chat', 'CHAT_COMMAND_RESULT', { user, cmd: msg.split(/\s+/)[0].toLowerCase(), result });
    } else {
      // ── Non-command message ───────────────────────────────────────────────
      const intent = classifyIntent(msg);
      appendEvent('trace:chat', 'CHAT_INTENT', { user, intent: intent.intent, confidence: intent.confidence });
      ndjson.write('trace:chat', 'CHAT_INTENT', { user, intent: intent.intent, confidence: intent.confidence });

      const fastResponse = responseTemplate(intent.intent, user);
      if (fastResponse) {
        // Fast-path deterministic template — always fires regardless of mode
        metrics.incFastPath();
        speakAndLog(fastResponse, {}, { source: 'intent-fast-path', intent: intent.intent });
      } else if (conversational.isEnabled()) {
        // Conversational mode ON — route to LLM ambient
        const rateCheck = conversational.checkRateLimit(user);
        if (!rateCheck.allowed) {
          metrics.incConvRateLimited();
          ndjson.write('conversational:rate_limit', 'CONV_RATE_LIMITED', { user, reason: rateCheck.reason });
        } else {
          metrics.incConversational();
          conversational.markResponded(user);
          ndjson.write('conversational:reply', 'CONV_REPLY_START', { user, msg: msg.slice(0, 200) });
          await route(msg, { ...ctx, user, role: ctx.role });
          ndjson.write('conversational:reply', 'CONV_REPLY_COMPLETE', { user });
        }
      }
      // else: conversational OFF + no fast-path match → silently ignore non-command chat
    }

    appendEvent('trace:chat', 'CHAT_RESPONSE_COMPLETE', { user, msg: msg.slice(0, 120) });
    ndjson.write('trace:chat', 'CHAT_RESPONSE_COMPLETE', { user });
  }

  const processLatency = Date.now() - started;
  metrics.observeProcessLatency(processLatency);
  appendEvent('trace:queue', 'EVENT_PROCESSED', {
    type: ev.type,
    user,
    process_latency_ms: processLatency,
    queue_size: eventQueue.size(),
  });
}

async function startEventLoop() {
  while (!shuttingDown) {
    const ev = await eventQueue.dequeue();
    try {
      sleeper.wake();
      await processEvent(ev);
      globalDag.state === 'LISTENING' && globalDag.transition('IDLE');
    } catch (e) {
      metrics.incError();
      appendEvent('agent:main', 'EVENT_PROCESS_ERROR', {
        type: ev.type,
        error: e.message,
      });
      globalDag.transition('ERROR', { error: e.message, type: ev.type });
      globalDag.reset();
    }
  }
}

function scheduleReconnect(reason = 'unknown') {
  if (shuttingDown || !reconnectCfg.enabled || reconnectTimer) return;
  const delay = Math.min(
    reconnectCfg.maxDelayMs,
    reconnectCfg.baseDelayMs * (2 ** reconnectAttempt)
  );

  appendEvent('agent:main', 'RECONNECT_SCHEDULED', {
    reason,
    attempt: reconnectAttempt + 1,
    delay_ms: delay,
  });

  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    reconnectAttempt += 1;
    connectLive().catch(err => {
      appendEvent('agent:main', 'RECONNECT_FAILED', {
        attempt: reconnectAttempt,
        error: err.message,
      });
      scheduleReconnect('connect-failure');
    });
  }, delay);
}

function bindConnectionHandlers(conn) {
  conn.on('member', data => enqueueEvent('member', data, priorities.member));

  conn.on('chat', data => {
    const msg = String(data.comment || '').trim();
    if (!msg) return;
    if (msg.startsWith('/')) {
      enqueueEvent('command', data, priorities.command);
    } else {
      enqueueEvent('chat', data, priorities.chat);
    }
  });

  conn.on('gift', data => enqueueEvent('gift', data, priorities.gift));
  conn.on('like', data => enqueueEvent('like', data, priorities.like));
  conn.on('share', data => enqueueEvent('share', data, priorities.share));

  // The connector may emit follow in newer builds; keep it optional.
  conn.on('follow', data => enqueueEvent('follow', data, priorities.follow));

  conn.on('error', err => {
    metrics.incError();
    appendEvent('agent:main', 'TIKTOK_ERROR', { error: err.message });
    globalDag.transition('ERROR', { error: err.message });
    globalDag.reset();
    scheduleReconnect('error');
  });

  conn.on('disconnected', () => {
    appendEvent('agent:main', 'DISCONNECTED', {});
    globalDag.reset();
    scheduleReconnect('disconnected');
  });
}

async function connectLive() {
  tiktok = new TikTokConn(USERNAME);
  bindConnectionHandlers(tiktok);
  await tiktok.connect();
  reconnectAttempt = 0;
  appendEvent('agent:main', 'CONNECTED', { username: USERNAME });
  console.log(`[agent] Connected to @${USERNAME}`);
  globalDag.transition('LISTENING', { source: 'tiktok' });
}

appendEvent('agent:main', 'AGENT_STARTED', { mode: 'live', persona: personaName, username: USERNAME });
ndjson.write('agent:main', 'AGENT_STARTED', {
  mode: 'live', persona: personaName, username: USERNAME,
  conversational: conversational.status(),
  ndjson: ndjson.status(),
});

startEventLoop();

const metricInterval = setInterval(() => {
  const snap = { ...metrics.snapshot(), queueDepth: eventQueue.size() };
  appendEvent('metrics:runtime', 'METRICS_SNAPSHOT', snap);
  ndjson.write('metrics:runtime', 'METRICS_SNAPSHOT', snap);
}, config.runtime?.metricsLogIntervalMs || 30000);

connectLive().catch(err => {
  appendEvent('agent:main', 'CONNECT_ERROR', { error: err.message });
  scheduleReconnect('initial-connect-failed');
});

process.on('SIGINT', () => {
  console.log('\n[agent] Shutting down…');
  shuttingDown = true;
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  clearInterval(metricInterval);
  sleeper.destroy();
  openclaw.stop();
  const finalMetrics = metrics.snapshot();
  appendEvent('agent:main', 'AGENT_STOPPED', {
    uptime_ms: process.uptime() * 1000,
    metrics: finalMetrics,
  });
  ndjson.write('agent:main', 'AGENT_STOPPED', {
    uptime_ms: process.uptime() * 1000,
    metrics: finalMetrics,
  });
  ndjson.close();
  process.exit(0);
});
