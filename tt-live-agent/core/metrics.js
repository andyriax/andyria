'use strict';

class Metrics {
  constructor() {
    this.startedAt = Date.now();
    this.events = 0;
    this.responses = 0;
    this.errors = 0;
    this.dropped = 0;
    this.byType = {};
    this.queueLatencyTotal = 0;
    this.queueLatencyCount = 0;
    this.processLatencyTotal = 0;
    this.processLatencyCount = 0;
    // Conversational mode counters
    this.conversationalHits = 0;   // chat messages routed to LLM ambient
    this.fastPathHits       = 0;   // chat messages answered by intent templates
    this.commandHits        = 0;   // slash commands executed
    this.convRateLimited    = 0;   // conversational replies blocked by rate limit
  }

  incEvent(type) {
    this.events += 1;
    this.byType[type] = (this.byType[type] || 0) + 1;
  }

  incResponse() {
    this.responses += 1;
  }

  incError() {
    this.errors += 1;
  }

  incDropped() {
    this.dropped += 1;
  }

  incConversational() {
    this.conversationalHits += 1;
  }

  incFastPath() {
    this.fastPathHits += 1;
  }

  incCommand() {
    this.commandHits += 1;
  }

  incConvRateLimited() {
    this.convRateLimited += 1;
  }

  observeQueueLatency(ms) {
    this.queueLatencyTotal += ms;
    this.queueLatencyCount += 1;
  }

  observeProcessLatency(ms) {
    this.processLatencyTotal += ms;
    this.processLatencyCount += 1;
  }

  snapshot() {
    const uptimeMs = Date.now() - this.startedAt;
    const avgQueueLatencyMs = this.queueLatencyCount
      ? this.queueLatencyTotal / this.queueLatencyCount
      : 0;
    const avgProcessLatencyMs = this.processLatencyCount
      ? this.processLatencyTotal / this.processLatencyCount
      : 0;

    const totalChat = this.conversationalHits + this.fastPathHits;
    const conversationalHitRate = totalChat > 0
      ? this.conversationalHits / totalChat
      : 0;

    return {
      uptimeMs,
      events: this.events,
      responses: this.responses,
      errors: this.errors,
      dropped: this.dropped,
      responseRate: this.events ? this.responses / this.events : 0,
      avgQueueLatencyMs,
      avgProcessLatencyMs,
      byType: { ...this.byType },
      // Engagement breakdown
      commandHits        : this.commandHits,
      fastPathHits       : this.fastPathHits,
      conversationalHits : this.conversationalHits,
      convRateLimited    : this.convRateLimited,
      conversationalHitRate,
    };
  }
}

module.exports = { Metrics };
