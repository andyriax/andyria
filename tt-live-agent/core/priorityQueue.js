'use strict';

class AsyncPriorityQueue {
  constructor() {
    this._items = [];
    this._waiters = [];
    this._seq = 0;
  }

  enqueue(item, priority = 0) {
    const wrapped = {
      ...item,
      _priority: Number.isFinite(priority) ? priority : 0,
      _seq: this._seq++,
    };

    if (this._waiters.length > 0) {
      const next = this._waiters.shift();
      next(wrapped);
      return;
    }

    this._items.push(wrapped);
    this._items.sort((a, b) => {
      if (b._priority !== a._priority) return b._priority - a._priority;
      return a._seq - b._seq;
    });
  }

  async dequeue() {
    if (this._items.length > 0) {
      return this._items.shift();
    }

    return new Promise(resolve => {
      this._waiters.push(resolve);
    });
  }

  size() {
    return this._items.length;
  }
}

module.exports = { AsyncPriorityQueue };
