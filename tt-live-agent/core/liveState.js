'use strict';

class LiveState {
  constructor(config = {}) {
    this._cfg = {
      greetRateLimitMs: config.greetRateLimitMs || 90_000,
      returningPriorityVisits: config.returningPriorityVisits || 2,
      highValueCoinsThreshold: config.highValueCoinsThreshold || 200,
      spamWindowMs: config.spamWindowMs || 10_000,
      spamMaxMessages: config.spamMaxMessages || 6,
      duplicateMessageWindowMs: config.duplicateMessageWindowMs || 8_000,
      historySize: config.historySize || 50,
    };

    this.users = new Map();
    this.recentMessages = [];
  }

  _getUser(user) {
    if (!this.users.has(user)) {
      this.users.set(user, {
        user,
        visits: 0,
        lastSeenAt: 0,
        lastGreetAt: 0,
        lastMessageAt: 0,
        msgCountWindow: [],
        totalCoins: 0,
        role: 'viewer',
      });
    }
    return this.users.get(user);
  }

  upsertUser(user, meta = {}) {
    const now = Date.now();
    const s = this._getUser(user);

    const wasAway = now - (s.lastSeenAt || 0) > this._cfg.greetRateLimitMs;
    if (s.lastSeenAt === 0 || wasAway) s.visits += 1;

    s.lastSeenAt = now;
    if (meta.role) s.role = meta.role;
    return s;
  }

  recordMessage(user, message, role = 'viewer') {
    const now = Date.now();
    const s = this.upsertUser(user, { role });
    s.lastMessageAt = now;

    s.msgCountWindow = s.msgCountWindow.filter(ts => now - ts <= this._cfg.spamWindowMs);
    s.msgCountWindow.push(now);

    const normalized = (message || '').trim().toLowerCase();
    if (normalized) {
      this.recentMessages.push({ user, normalized, ts: now });
      this.recentMessages = this.recentMessages
        .filter(m => now - m.ts <= this._cfg.duplicateMessageWindowMs)
        .slice(-this._cfg.historySize);
    }

    const isSpamRate = s.msgCountWindow.length > this._cfg.spamMaxMessages;
    const duplicateCount = this.recentMessages.filter(
      m => m.user === user && m.normalized === normalized
    ).length;

    return {
      isSpam: isSpamRate || duplicateCount >= 3,
      duplicateCount,
      messageRate: s.msgCountWindow.length,
    };
  }

  shouldGreet(user) {
    const now = Date.now();
    const s = this._getUser(user);
    return now - (s.lastGreetAt || 0) >= this._cfg.greetRateLimitMs;
  }

  markGreeted(user) {
    const s = this._getUser(user);
    s.lastGreetAt = Date.now();
  }

  recordGift(user, coins = 0) {
    const s = this._getUser(user);
    s.totalCoins += Number(coins) || 0;
    return s.totalCoins;
  }

  getRole(user) {
    return this._getUser(user).role || 'viewer';
  }

  getUserSummary(user) {
    const s = this._getUser(user);
    return {
      user,
      visits: s.visits,
      totalCoins: s.totalCoins,
      role: s.role,
      lastSeenAt: s.lastSeenAt,
    };
  }
}

module.exports = { LiveState };
