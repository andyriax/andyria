'use strict';

const { format }      = require('../core/persona');
const { appendEvent } = require('../core/db');

module.exports = {
  command    : '/shoutout',
  description: 'Give a shoutout to a specific user',

  execute(args, ctx) {
    const target = args[0] || ctx.user;
    const msg    = format(
      '🎤 Shoutout to {target}! Show them some love in the comments!',
      { target }
    );
    ctx.speak(msg);
    appendEvent('skill:shoutout', 'SHOUTOUT_SENT', { target, caller: ctx.user });
  },
};
