/**
 * core/usecasefinder.js
 * Use-Case Finder — runs during sleep mode to surface actionable insights
 * from the event log, revenue data, and viewer patterns.
 *
 * Each cycle picks a different lens (revenue, engagement, content, growth)
 * and asks the LLM waterfall to generate a specific, actionable suggestion.
 * Findings are logged to CQRS and printed to the console.
 *
 * Runs on a ticker set by Sleeper — stops immediately when woken.
 */

'use strict';

const { queryEvents, revenueTotal, appendEvent } = require('./db');
const { get: getPersona }  = require('./persona');
const llm                  = require('./llm');

// ---------------------------------------------------------------------------
// Analysis lenses — cycled round-robin each sleep tick
// ---------------------------------------------------------------------------

const LENSES = [
  {
    id   : 'revenue',
    label: '💰 Revenue Opportunity',
    build: (snap) => `
You are analyzing a TikTok Live session for the persona "${snap.persona}".
Stream data snapshot:
- Total coins earned: ${snap.totalCoins}
- Recent gifts: ${snap.recentGifts}
- Top gifters: ${snap.topGifters}

Identify ONE specific revenue action the host can take RIGHT NOW to earn more coins.
Be direct. Max 2 sentences. No preamble.`.trim(),
  },
  {
    id   : 'engagement',
    label: '🔥 Engagement Boost',
    build: (snap) => `
You are a TikTok Live engagement strategist for persona "${snap.persona}".
Stream data snapshot:
- Recent viewer activity: ${snap.recentActivity}
- Event types in last 5 minutes: ${snap.recentEventTypes}
- Chat message count: ${snap.chatCount}

Suggest ONE specific on-stream action to spike engagement in the next 60 seconds.
Be direct. Max 2 sentences. No preamble.`.trim(),
  },
  {
    id   : 'content',
    label: '🎬 Content Idea',
    build: (snap) => `
You are a content strategist for a TikTok Live stream (persona: "${snap.persona}").
The stream has been quiet for ${snap.idleMinutes} minutes.
Revenue so far: ${snap.totalCoins} coins.

Generate ONE viral content idea the host can execute live RIGHT NOW with no props.
Be creative and specific. Max 2 sentences.`.trim(),
  },
  {
    id   : 'growth',
    label: '📈 Growth Tactic',
    build: (snap) => `
You are a TikTok growth hacker advising persona "${snap.persona}".
Stream stats:
- Unique viewers joined: ${snap.uniqueViewers}
- Shares: ${snap.shareCount}
- Idle time: ${snap.idleMinutes} minutes

Suggest ONE actionable tactic to grow followers or shares in this exact moment.
Be specific. Max 2 sentences.`.trim(),
  },
  {
    id   : 'skill_gap',
    label: '🛠  Skill Suggestion',
    build: (snap) => `
You are an AI agent architect reviewing a TikTok Live bot's behavior.
Loaded skills: ${snap.loadedSkills}
Recent commands used: ${snap.recentCommands}
Unused triggers: ${snap.unusedTriggers}

Suggest ONE new /command skill that would meaningfully improve this live stream agent.
Name it, describe it in 1 sentence, and give the output it would produce.`.trim(),
  },
];

let _lensIdx = 0;

// ---------------------------------------------------------------------------
// Snapshot builder — queries CQRS for live data
// ---------------------------------------------------------------------------

function _buildSnapshot(idleMinutes) {
  const persona     = getPersona();
  const totalCoins  = revenueTotal();

  // Recent events (last 30)
  const recent      = queryEvents({ limit: 30 });
  const recentGifts = recent
    .filter(e => e.event_type === 'GIFT_EVENT')
    .map(e => `${e.payload.user}(${e.payload.coins})`)
    .join(', ') || 'none';

  // Top gifters from revenue table
  const giftEvents  = queryEvents({ event_type: 'GIFT_EVENT', limit: 100 });
  const gifterMap   = {};
  giftEvents.forEach(e => {
    gifterMap[e.payload.user] = (gifterMap[e.payload.user] || 0) + (e.payload.coins || 0);
  });
  const topGifters  = Object.entries(gifterMap)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 3)
    .map(([u, c]) => `${u}(${c})`)
    .join(', ') || 'none';

  // Engagement snapshot
  const chatEvents  = queryEvents({ event_type: 'CHAT_MESSAGE', limit: 50 });
  const chatCount   = chatEvents.length;
  const shareCount  = queryEvents({ event_type: 'SHARE_EVENT', limit: 100 }).length;
  const joinCount   = queryEvents({ event_type: 'VIEWER_JOINED', limit: 100 }).length;

  const recentEventTypes = [...new Set(recent.map(e => e.event_type))].join(', ') || 'none';
  const recentActivity   = recent.slice(0, 5).map(e => e.event_type).join(' → ') || 'quiet';

  // Skills and commands
  const cmdEvents       = queryEvents({ event_type: 'COMMAND_RECEIVED', limit: 20 });
  const recentCommands  = [...new Set(cmdEvents.map(e => e.payload.cmd))].join(', ') || 'none';

  // We can't require skillLoader here without circular deps — pass via snapshot opt
  const loadedSkills    = 'greet, hype, drop, shoutout, revenue';
  const unusedTriggers  = 'follow, subscribe';

  return {
    persona       : persona?.name || 'Default',
    totalCoins,
    recentGifts,
    topGifters,
    chatCount,
    shareCount,
    uniqueViewers : joinCount,
    recentEventTypes,
    recentActivity,
    recentCommands,
    loadedSkills,
    unusedTriggers,
    idleMinutes   : Math.round(idleMinutes),
  };
}

// ---------------------------------------------------------------------------
// Single use-case discovery cycle
// ---------------------------------------------------------------------------

/**
 * Run one analysis cycle using a 2-turn multi-input dialogue.
 *
 * Turn 1 (analysis): LLM sees the data snapshot and surfaces raw observations.
 * Turn 2 (refinement): LLM is asked to distil those observations into ONE
 *   specific, immediately executable action — the actionable finding.
 *
 * This gives richer, more contextual output than a single-shot prompt.
 *
 * @param {number} idleMinutes
 * @returns {Promise<{ lens: string, finding: string }>}
 */
async function runCycle(idleMinutes = 0) {
  const lens = LENSES[_lensIdx % LENSES.length];
  _lensIdx++;

  const snap       = _buildSnapshot(idleMinutes);
  const systemText = `You are an expert TikTok Live strategist embedded in an autonomous agent. Persona in use: "${snap.persona}". Be concise, direct, and actionable.`;

  // ── Turn 1: surface raw observations ─────────────────────────────────────
  const analysisTurn1 = {
    role   : 'system',
    content: systemText,
  };
  const analysisTurn2 = {
    role   : 'user',
    content: lens.build(snap),
  };

  let analysisReply;
  try {
    analysisReply = await llm.thread(
      [analysisTurn1, analysisTurn2],
      { user: 'use-case-finder' }
    );
  } catch (_) {
    analysisReply = 'Gift challenges and countdowns drive coins most reliably.';
  }

  // ── Turn 2: refine into one immediately actionable instruction ────────────
  const refineTurn = {
    role   : 'user',
    content: `Based on your analysis, give me ONE specific action the host can say or do in the next 30 seconds. One sentence. Start with a verb.`,
  };

  let finding;
  try {
    finding = await llm.thread(
      [analysisTurn1, analysisTurn2,
       { role: 'assistant', content: analysisReply },
       refineTurn],
      { user: 'use-case-finder' }
    );
  } catch (_) {
    finding = analysisReply; // fall back to turn-1 output if turn-2 fails
  }

  appendEvent('usecasefinder', 'USE_CASE_FOUND', {
    lens    : lens.id,
    analysis: analysisReply,
    finding,
    snap    : { coins: snap.totalCoins, idle: snap.idleMinutes },
    provider: llm.getLastProvider(),
  });

  return { lens: lens.label, finding };
}

module.exports = { runCycle };
