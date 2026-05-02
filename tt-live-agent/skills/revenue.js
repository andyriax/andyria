/**
 * skills/revenue.js
 * Autonomous revenue behaviors — fires on gift events, milestones, and
 * periodic drops. Self-triggers without requiring a /command from chat.
 *
 * Behaviors:
 *   /revenue stats  → read current revenue total
 *   /revenue drop   → manual product drop
 *   /revenue unlock → announce gift-unlocked content
 */

'use strict';

const { logRevenue, revenueTotal, getMemory } = require('../core/db');
const { format }       = require('../core/persona');
const { appendEvent }  = require('../core/db');
const { announceGift } = require('../core/voice');

// Milestone thresholds → announcement message
const MILESTONES = [
  { coins: 500,   msg: 'We hit {coins} coins! 🚀 The community is on fire!' },
  { coins: 1000,  msg: '{coins} coins reached! You all are LEGENDS 🏆' },
  { coins: 5000,  msg: '5K COINS! This is unreal. Thank you {coins} coin family! 💎' },
  { coins: 10000, msg: '10K COINS. Hall of fame. {coins} coins. WOW.' },
];

let _lastMilestoneCrossed = 0;

module.exports = {
  command    : '/revenue',
  description: 'Revenue stats, product drops, and gift-unlock announcements',

  execute(args, ctx) {
    const sub = (args[0] || 'stats').toLowerCase();

    if (sub === 'stats') {
      const total = revenueTotal();
      ctx.speak(format('Total coins earned: {coins} 💰', { coins: total }));
      appendEvent('skill:revenue', 'STATS_REQUESTED', { total, user: ctx.user });
      return;
    }

    if (sub === 'drop') {
      const cfg  = getMemory('config') || {};
      const link = cfg.revenue?.shop_link || 'link in bio';
      ctx.speak(format('🛒 EXCLUSIVE DROP — {link} — Act fast!', { link }));
      appendEvent('skill:revenue', 'DROP_TRIGGERED', { user: ctx.user });
      return;
    }

    if (sub === 'unlock') {
      const target = args[1] || ctx.user;
      ctx.speak(format(
        '🔓 {user} unlocked exclusive content! Check the link in bio!',
        { user: target }
      ));
      appendEvent('skill:revenue', 'UNLOCK_ANNOUNCED', { user: target });
      return;
    }
  },
};

// ---------------------------------------------------------------------------
// Autonomous gift handler (called from agent.js on 'gift' events)
// ---------------------------------------------------------------------------

/**
 * Handle a TikTok gift event autonomously.
 * @param {object} data  - gift event payload from tiktok-live-connector
 * @param {object} ctx   - { speak, config }
 */
function handleGift(data, ctx) {
  const user  = data.uniqueId || 'viewer';
  const coins = data.diamondCount || data.repeatCount || 1;

  // Log to revenue ledger
  logRevenue(user, 'GIFT', coins);
  appendEvent('revenue:gift', 'GIFT_RECEIVED', { user, coins });

  // Voice announcement
  if (ctx?.config?.revenue?.auto_drop_on_gift) {
    announceGift(user, coins);
  }

  // Milestone check
  const total = revenueTotal();
  for (const m of MILESTONES) {
    if (total >= m.coins && _lastMilestoneCrossed < m.coins) {
      _lastMilestoneCrossed = m.coins;
      const msg = format(m.msg, { coins: m.coins });
      if (ctx?.speak) ctx.speak(msg);
      appendEvent('revenue:milestone', 'MILESTONE_REACHED', { milestone: m.coins, total });
      break;
    }
  }

  // Auto-drop if threshold exceeded
  const threshold = ctx?.config?.revenue?.gift_threshold_coins || 100;
  if (coins >= threshold) {
    const link = ctx?.config?.revenue?.shop_link || 'link in bio';
    if (ctx?.speak) {
      ctx.speak(format(
        '{user} sent a massive gift! 🎁 Grab the exclusive link: {link}',
        { user, link }
      ));
    }
    appendEvent('revenue:autodrop', 'AUTO_DROP_TRIGGERED', { user, coins, link });
  }
}

module.exports.handleGift = handleGift;
