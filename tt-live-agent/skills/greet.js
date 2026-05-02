'use strict';

const { format } = require('../core/persona');
const { appendEvent } = require('../core/db');

module.exports = {
  command    : '/greet',
  description: 'Greet a viewer by name',

  execute(args, ctx) {
    const target = args[0] || ctx.user;
    const msg    = format('Welcome {user}! Glad you joined the stream! 🎉', { user: target });
    ctx.speak(msg);
    appendEvent(`skill:greet`, 'GREET_SENT', { user: target });
    if (ctx.log) ctx.log('GREET_SENT', { user: target });
  },
};
