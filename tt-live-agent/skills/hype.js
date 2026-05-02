'use strict';

const { format } = require('../core/persona');
const { appendEvent } = require('../core/db');

module.exports = {
  command    : '/hype',
  description: 'Fire a hype message at the current viewer',

  execute(args, ctx) {
    const msg = format("LET'S GO {user}!!! The energy is INSANE right now 🔥🔥🔥", {
      user: ctx.user,
    });
    ctx.speak(msg);
    appendEvent('skill:hype', 'HYPE_SENT', { user: ctx.user });
  },
};
