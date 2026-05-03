'use strict';

function classifyIntent(message = '') {
  const text = message.trim().toLowerCase();
  if (!text) return { intent: 'empty', confidence: 1.0 };

  if (text.startsWith('/')) return { intent: 'command', confidence: 1.0 };
  if (/\b(help|how|what|where|commands?)\b/.test(text)) return { intent: 'help', confidence: 0.8 };
  if (/\b(game|play|challenge|quiz)\b/.test(text)) return { intent: 'game', confidence: 0.8 };
  if (/\b(shoutout|shout out|notice me)\b/.test(text)) return { intent: 'shoutout', confidence: 0.8 };
  if (/\b(gift|coins|donate|drop)\b/.test(text)) return { intent: 'monetization', confidence: 0.75 };
  if (/\?+$/.test(text) || /^why\b|^how\b|^what\b/.test(text)) return { intent: 'question', confidence: 0.7 };
  if (/\b(hi|hey|hello|yo|sup)\b/.test(text)) return { intent: 'greeting', confidence: 0.65 };
  return { intent: 'chat', confidence: 0.55 };
}

function responseTemplate(intent, user) {
  switch (intent) {
    case 'help':
      return `@${user} commands: /help /stats /shoutout @user /game /revenue stats`;
    case 'game':
      return `@${user} type /game to start the lightning quiz.`;
    case 'shoutout':
      return `@${user} use /shoutout @username and I will call them out live.`;
    case 'monetization':
      return `@${user} gifts unlock reactions and milestone calls, keep it coming.`;
    case 'greeting':
      return `@${user} welcome in, glad you are here.`;
    default:
      return '';
  }
}

module.exports = { classifyIntent, responseTemplate };
