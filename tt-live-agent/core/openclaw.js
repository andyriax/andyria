/**
 * core/openclaw.js — OpenClaw Self-Explorer
 *
 * Autonomously discovers capability gaps in the running agent and
 * auto-implements new skills to fill them. Zero configuration needed.
 *
 * Discovery phases per cycle:
 *   1. DISCOVER   — scan skills/, agents/, personas/ + CQRS event log
 *   2. ANALYSE    — identify gaps (missing skills, unhandled demand, etc.)
 *   3. ASSUME     — LLM infers purpose + implementation from context
 *   4. IMPLEMENT  — LLM writes skill JS, node --check validates, file saved
 *   5. RELOAD     — hot-reload skill registry
 *
 * Usage:
 *   openclaw.start()           — background loop, re-scans every 5 min
 *   openclaw.runCycle()        — single discovery + implementation pass
 *   openclaw.runCycle({dryRun:true})  — discovery only, no writes
 *   openclaw.stop()            — stop background loop
 */

'use strict';

const fs   = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');

const { queryEvents, appendEvent } = require('./db');
const { loadSkills }               = require('./skillLoader');
const llm                          = require('./llm');

const ROOT        = path.join(__dirname, '..');
const SKILLS_DIR  = path.join(ROOT, 'skills');
const AGENTS_DIR  = path.join(ROOT, 'agents');
const PERSONAS_DIR = path.join(ROOT, 'personas');

const SCAN_INTERVAL_MS = 5 * 60 * 1000;   // re-scan every 5 min
const MAX_PER_CYCLE    = 3;                // max new skills per cycle

let _running   = false;
let _scanTimer = null;

// Callback for pushing OpenClaw findings to the sleep screen
let _pushFn = null;

function setPushFn(fn) { _pushFn = fn; }

function _push(message) {
  console.log(`[openclaw] ${message}`);
  if (_pushFn) _pushFn('openclaw', { message });
}

// ---------------------------------------------------------------------------
// Phase 1: Discovery
// ---------------------------------------------------------------------------

function discoverSkills() {
  if (!fs.existsSync(SKILLS_DIR)) return [];
  return fs.readdirSync(SKILLS_DIR)
    .filter(f => f.endsWith('.js') && !f.endsWith('.tmp'))
    .map(f => {
      const src      = fs.readFileSync(path.join(SKILLS_DIR, f), 'utf8');
      const cmdMatch = src.match(/command\s*[:=]\s*['"]([^'"]+)['"]/);
      const dscMatch = src.match(/description\s*[:=]\s*['"]([^'"]+)['"]/);
      return {
        file: f,
        cmd : cmdMatch?.[1] || '/' + f.replace('.js', ''),
        desc: dscMatch?.[1] || 'no description',
      };
    });
}

function discoverAgents() {
  if (!fs.existsSync(AGENTS_DIR)) return [];
  return fs.readdirSync(AGENTS_DIR)
    .filter(f => f.endsWith('.json'))
    .flatMap(f => {
      try {
        const data = JSON.parse(fs.readFileSync(path.join(AGENTS_DIR, f), 'utf8'));
        return [{ file: f, ...data }];
      } catch (_) { return []; }
    });
}

function discoverPersonas() {
  if (!fs.existsSync(PERSONAS_DIR)) return [];
  return fs.readdirSync(PERSONAS_DIR)
    .filter(f => f.endsWith('.json'))
    .map(f => f.replace('.json', ''));
}

function discoverChatDemand() {
  // Commands users actually typed (from CQRS)
  const events = queryEvents({ event_type: 'COMMAND_RECEIVED', limit: 300 });
  const counts = {};
  events.forEach(e => {
    const c = e.payload?.cmd || '?';
    counts[c] = (counts[c] || 0) + 1;
  });
  return Object.entries(counts)
    .sort((a, b) => b[1] - a[1])
    .map(([cmd, count]) => ({ cmd, count }));
}

function discoverAlreadyImplemented() {
  return queryEvents({ event_type: 'OPENCLAW_IMPLEMENTED', limit: 50 })
    .map(e => e.payload?.cmd)
    .filter(Boolean);
}

function discoverEventPatterns() {
  // What TikTok event types are actually occurring?
  return new Set(queryEvents({ limit: 500 }).map(e => e.event_type));
}

// ---------------------------------------------------------------------------
// Phase 2: Gap analysis
// ---------------------------------------------------------------------------

function analyseGaps(skills, agents, chatDemand, implemented) {
  const gaps      = [];
  const skillCmds = new Set(skills.map(s => s.cmd));
  const done      = new Set(implemented);

  // ── Gap type A: user-demanded commands not yet handled ──────────────────
  chatDemand.forEach(({ cmd, count }) => {
    if (cmd.startsWith('/') && !skillCmds.has(cmd) && !done.has(cmd) && count >= 2) {
      gaps.push({
        type    : 'user_demand',
        cmd,
        priority: count,
        reason  : `Users requested "${cmd}" ${count}x — no skill handles it`,
      });
    }
  });

  // ── Gap type B: skills referenced in agent definitions but missing ───────
  agents.forEach(agent => {
    (agent.skills || []).forEach(cmd => {
      if (!skillCmds.has(cmd) && !done.has(cmd)) {
        gaps.push({
          type    : 'agent_binding',
          cmd,
          priority: 10,
          agent   : agent.id || agent.file,
          reason  : `Agent "${agent.id || agent.file}" references "${cmd}" but it doesn't exist`,
        });
      }
    });
  });

  // ── Gap type C: common TikTok events with no dedicated skill ─────────────
  const eventTypes = discoverEventPatterns();
  const wantedMap  = {
    FOLLOW_EVENT   : '/follow',
    SUBSCRIBE_EVENT: '/subscribe',
    QUESTION_EVENT : '/answer',
    POLL_EVENT     : '/poll',
    MILESTONE_EVENT: '/milestone',
  };
  Object.entries(wantedMap).forEach(([evt, cmd]) => {
    if (eventTypes.has(evt) && !skillCmds.has(cmd) && !done.has(cmd)) {
      gaps.push({
        type    : 'event_handler',
        cmd,
        priority: 5,
        event   : evt,
        reason  : `"${evt}" events are occurring but no "${cmd}" skill responds to them`,
      });
    }
  });

  // Sort: highest priority first
  return gaps.sort((a, b) => b.priority - a.priority);
}

// ---------------------------------------------------------------------------
// Phase 3 + 4: Assume + Implement
// ---------------------------------------------------------------------------

function _exampleSkill() {
  // Provide greet.js as a few-shot example for the LLM
  try {
    const src = fs.readFileSync(path.join(SKILLS_DIR, 'greet.js'), 'utf8');
    return src.slice(0, 900);
  } catch (_) {
    return `'use strict';
const { format } = require('../core/persona');
module.exports = {
  command    : '/example',
  description: 'Example skill',
  execute(args, ctx) {
    ctx.speak(format('Hello {user}!', { user: ctx.user }));
  },
};`;
  }
}

async function implementGap(gap, skills) {
  const example   = _exampleSkill();
  const skillList = skills.map(s => `${s.cmd} (${s.desc})`).join(', ');

  // ── Turn 1: describe what the skill should do ────────────────────────────
  const systemMsg = {
    role   : 'system',
    content: [
      'You are an expert Node.js developer writing skills for a TikTok Live AI agent.',
      'Skills are CommonJS modules. ctx has: ctx.speak(text), ctx.user (string), ctx.config, ctx.dag.',
      'Keep skills short, engaging, and appropriate for live streaming.',
      'Respond with plain JavaScript only — no markdown fences, no explanation.',
    ].join(' '),
  };

  const descTurn = {
    role   : 'user',
    content: `Existing skills for reference: ${skillList}\n\nI need a new skill for: ${gap.cmd}\nReason: ${gap.reason}\n\nDescribe what this skill should do in 1-2 sentences.`,
  };

  let desc;
  try {
    desc = await llm.thread([systemMsg, descTurn], { user: 'openclaw' });
  } catch (_) {
    desc = `Handles the ${gap.cmd} command on TikTok Live, responding to viewers appropriately.`;
  }

  // ── Turn 2: generate the code ────────────────────────────────────────────
  const codeTurn = {
    role   : 'user',
    content: `Example skill for reference:\n${example}\n\nNow write the complete skill JS for "${gap.cmd}".\nDescription: ${desc}\nOutput ONLY valid JavaScript. No markdown. Use module.exports with command, description, execute(args, ctx).`,
  };

  let code;
  try {
    code = await llm.thread(
      [systemMsg, descTurn, { role: 'assistant', content: desc }, codeTurn],
      { user: 'openclaw' }
    );
  } catch (e) {
    return { success: false, reason: `LLM error: ${e.message}` };
  }

  // Strip markdown fences if the LLM wrapped the code
  code = code
    .replace(/^```(?:javascript|js)?\s*/im, '')
    .replace(/\s*```\s*$/im, '')
    .trim();

  // Ensure the generated code declares the right command
  if (!code.includes(gap.cmd)) {
    // Inject command field if missing (best-effort)
    code = code.replace(/command\s*[:=]\s*['"][^'"]*['"]/, `command: '${gap.cmd}'`);
  }

  // ── Validate file name — no path traversal ───────────────────────────────
  const safeName = gap.cmd.replace(/[^a-z0-9_-]/gi, '').replace(/^\/+/, '') || 'unknown';
  const outFile  = path.join(SKILLS_DIR, `${safeName}.js`);
  const tmpFile  = outFile + '.tmp';

  // Never overwrite existing skills
  if (fs.existsSync(outFile)) {
    return { success: false, reason: `File already exists: ${safeName}.js` };
  }

  // Write to temp, syntax-check, then promote
  fs.writeFileSync(tmpFile, code, 'utf8');
  const check = spawnSync(process.execPath, ['--check', tmpFile], { encoding: 'utf8', timeout: 5000 });
  if (check.status !== 0) {
    fs.unlinkSync(tmpFile);
    return { success: false, reason: `Syntax error: ${(check.stderr || '').slice(0, 200)}` };
  }

  fs.renameSync(tmpFile, outFile);
  loadSkills();    // hot-reload

  appendEvent('openclaw', 'OPENCLAW_IMPLEMENTED', {
    cmd   : gap.cmd,
    file  : `${safeName}.js`,
    reason: gap.reason,
    desc,
  });

  return { success: true, cmd: gap.cmd, file: `${safeName}.js`, desc };
}

// ---------------------------------------------------------------------------
// Full discovery + implementation cycle
// ---------------------------------------------------------------------------

async function runCycle(opts = {}) {
  _push('🔍 Running self-exploration cycle...');

  const skills     = discoverSkills();
  const agents     = discoverAgents();
  const personas   = discoverPersonas();
  const chatDemand = discoverChatDemand();
  const implemented = discoverAlreadyImplemented();

  _push(`Found: ${skills.length} skills · ${agents.length} agents · ${personas.length} personas`);

  const gaps = analyseGaps(skills, agents, chatDemand, implemented);

  if (gaps.length === 0) {
    _push('✨ No gaps found — system is self-consistent!');
    appendEvent('openclaw', 'OPENCLAW_SCAN', { gaps: 0, skills: skills.length });
    return { gaps: [], results: [] };
  }

  _push(`Found ${gaps.length} gap(s):`);
  gaps.forEach(g => _push(`  • ${g.cmd} — ${g.reason}`));

  if (opts.dryRun) {
    return { gaps, results: [] };
  }

  // Implement top-priority gaps (max MAX_PER_CYCLE per run)
  const results = [];
  for (const gap of gaps.slice(0, MAX_PER_CYCLE)) {
    _push(`🔧 Implementing ${gap.cmd}...`);
    const result = await implementGap(gap, skills);
    results.push({ gap, ...result });
    if (result.success) {
      _push(`✅ ${gap.cmd} → skills/${result.file}`);
    } else {
      _push(`⚠️  ${gap.cmd} failed: ${result.reason}`);
    }
  }

  appendEvent('openclaw', 'OPENCLAW_SCAN', {
    gaps       : gaps.length,
    implemented: results.filter(r => r.success).length,
    skills     : skills.length,
  });

  return { gaps, results };
}

// ---------------------------------------------------------------------------
// Background loop (--self-explorer flag)
// ---------------------------------------------------------------------------

function start(opts = {}) {
  if (_running) return;
  _running = true;
  _push('🦅 OpenClaw self-explorer started — scanning every 5 min');

  runCycle(opts).catch(e => console.error('[openclaw] cycle error:', e.message));
  _scanTimer = setInterval(() => {
    if (_running) runCycle(opts).catch(e => console.error('[openclaw] cycle error:', e.message));
  }, SCAN_INTERVAL_MS);
}

function stop() {
  _running = false;
  if (_scanTimer) { clearInterval(_scanTimer); _scanTimer = null; }
  _push('Stopped.');
}

module.exports = {
  start, stop, runCycle, setPushFn,
  discoverSkills, discoverAgents, analyseGaps,
};
