'use strict';

const { getMemory } = require('../core/db');
const { format }    = require('../core/persona');
const { appendEvent } = require('../core/db');

module.exports = {
  command    : '/drop',
  description: 'Announce the product drop with shop link',

  execute(args, ctx) {
    const cfg  = getMemory('config') || {};
    const link = cfg.revenue?.shop_link || 'link in bio';
    const tag  = cfg.revenue?.affiliate_tag || '';
    const url  = tag ? `${link}?ref=${tag}` : link;

    const msg = format(
      'Link in bio. Limited stock. MOVE NOW 👆 {url}',
      { url }
    );
    ctx.speak(msg);
    appendEvent('skill:drop', 'DROP_ANNOUNCED', { url, user: ctx.user });
  },
};
