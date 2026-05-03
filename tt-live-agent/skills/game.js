'use strict';

const { appendEvent } = require('../core/db');

const QUESTIONS = [
  { q: 'Lightning quiz: what command shows metrics?', a: '/stats' },
  { q: 'Lightning quiz: what command gives command help?', a: '/help' },
  { q: 'Lightning quiz: what command triggers a shoutout?', a: '/shoutout' },
];

module.exports = {
  command: '/game',
  description: 'Run a quick viewer mini-game prompt',

  execute(args, ctx) {
    const idx = Math.floor(Math.random() * QUESTIONS.length);
    const selected = QUESTIONS[idx];
    const msg = `@${ctx.user} ${selected.q} Winner gets instant hype.`;
    ctx.speak(msg);
    appendEvent('skill:game', 'GAME_PROMPT', {
      user: ctx.user,
      question: selected.q,
      expected: selected.a,
    });
  },
};
