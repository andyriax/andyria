/**
 * core/llm.js
 * LLM provider waterfall — tries providers in order, falling back to the
 * next when a key is missing, quota is exhausted, or a request fails.
 *
 * Provider priority (highest to lowest cost / strictest to most permissive):
 *
 *  1. OpenAI          (paid, gpt-4o-mini)
 *  2. Anthropic        (paid, claude-haiku)
 *  3. Google Gemini    (FREE tier — gemini-1.5-flash, 15 rpm / 1M tpd)
 *  4. Groq             (FREE tier — llama-3.1-8b-instant, 30 rpm)
 *  5. OpenRouter       (FREE models — meta-llama/llama-3.2-3b-instruct:free)
 *  6. Ollama           (local, FREE — no internet required)
 *  7. HuggingFace      (FREE inference API — HuggingFaceH4/zephyr-7b-beta)
 *  8. Static fallback  (always works — persona-aware canned response)
 *
 * Configuration (config.json llm block):
 * {
 *   "llm": {
 *     "system_prompt": "You are a live stream host...",
 *     "max_tokens": 120,
 *     "providers": {
 *       "openai":      { "enabled": true,  "api_key": "sk-...",  "model": "gpt-4o-mini" },
 *       "anthropic":   { "enabled": true,  "api_key": "sk-ant-...", "model": "claude-haiku-4-5" },
 *       "gemini":      { "enabled": true,  "api_key": "AIza..." },
 *       "groq":        { "enabled": true,  "api_key": "gsk_..." },
 *       "openrouter":  { "enabled": true,  "api_key": "sk-or-..." },
 *       "ollama":      { "enabled": true,  "base_url": "http://localhost:11434", "model": "llama3.2" },
 *       "huggingface": { "enabled": true,  "api_key": "hf_..." }
 *     }
 *   }
 * }
 *
 * Any provider with a missing/empty api_key is automatically skipped.
 */

'use strict';

const https = require('https');
const http  = require('http');
const { appendEvent } = require('./db');
const { get: getPersona } = require('./persona');

// ---------------------------------------------------------------------------
// HTTP helper — wraps https/http POST as a Promise
// ---------------------------------------------------------------------------

function _post(url, headers, body) {
  return new Promise((resolve, reject) => {
    const parsed   = new URL(url);
    const isHttps  = parsed.protocol === 'https:';
    const mod      = isHttps ? https : http;
    const payload  = JSON.stringify(body);

    const req = mod.request({
      hostname: parsed.hostname,
      port    : parsed.port || (isHttps ? 443 : 80),
      path    : parsed.pathname + parsed.search,
      method  : 'POST',
      headers : { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(payload), ...headers },
    }, res => {
      let data = '';
      res.on('data', c => (data += c));
      res.on('end', () => {
        if (res.statusCode >= 400) {
          reject(new Error(`HTTP ${res.statusCode}: ${data.slice(0, 200)}`));
        } else {
          try { resolve(JSON.parse(data)); } catch (e) { reject(e); }
        }
      });
    });
    req.on('error', reject);
    req.setTimeout(8000, () => { req.destroy(new Error('LLM request timeout')); });
    req.write(payload);
    req.end();
  });
}

// ---------------------------------------------------------------------------
// Provider implementations
// ---------------------------------------------------------------------------

async function _openai(messages, cfg, maxTokens) {
  const key = cfg.api_key;
  if (!key) throw new Error('no key');
  const res = await _post(
    'https://api.openai.com/v1/chat/completions',
    { Authorization: `Bearer ${key}` },
    { model: cfg.model || 'gpt-4o-mini', messages, max_tokens: maxTokens, temperature: 0.8 }
  );
  return res.choices[0].message.content.trim();
}

async function _anthropic(messages, cfg, maxTokens) {
  const key = cfg.api_key;
  if (!key) throw new Error('no key');
  // Anthropic uses system separate from messages
  const system = messages.find(m => m.role === 'system')?.content || '';
  const conv   = messages.filter(m => m.role !== 'system');
  const res = await _post(
    'https://api.anthropic.com/v1/messages',
    { 'x-api-key': key, 'anthropic-version': '2023-06-01' },
    { model: cfg.model || 'claude-haiku-4-5', system, messages: conv, max_tokens: maxTokens }
  );
  return res.content[0].text.trim();
}

async function _gemini(messages, cfg, maxTokens) {
  const key = cfg.api_key;
  if (!key) throw new Error('no key');
  const model = cfg.model || 'gemini-1.5-flash';
  // Flatten messages → Gemini parts format
  const parts = messages.map(m => ({ text: `[${m.role}] ${m.content}` }));
  const res = await _post(
    `https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent?key=${key}`,
    {},
    { contents: [{ parts }], generationConfig: { maxOutputTokens: maxTokens, temperature: 0.8 } }
  );
  return res.candidates[0].content.parts[0].text.trim();
}

async function _groq(messages, cfg, maxTokens) {
  const key = cfg.api_key;
  if (!key) throw new Error('no key');
  const res = await _post(
    'https://api.groq.com/openai/v1/chat/completions',
    { Authorization: `Bearer ${key}` },
    { model: cfg.model || 'llama-3.1-8b-instant', messages, max_tokens: maxTokens, temperature: 0.8 }
  );
  return res.choices[0].message.content.trim();
}

async function _openrouter(messages, cfg, maxTokens) {
  const key = cfg.api_key;
  if (!key) throw new Error('no key');
  const res = await _post(
    'https://openrouter.ai/api/v1/chat/completions',
    {
      Authorization: `Bearer ${key}`,
      'HTTP-Referer': 'https://jetstreamin.io',
      'X-Title': 'tt-live-agent',
    },
    {
      model     : cfg.model || 'meta-llama/llama-3.2-3b-instruct:free',
      messages,
      max_tokens: maxTokens,
    }
  );
  return res.choices[0].message.content.trim();
}

async function _ollama(messages, cfg, maxTokens) {
  const baseUrl = cfg.base_url || 'http://localhost:11434';
  const model   = cfg.model || 'llama3.2';
  const prompt  = messages.map(m => `${m.role}: ${m.content}`).join('\n') + '\nassistant:';
  const res = await _post(
    `${baseUrl}/api/generate`,
    {},
    { model, prompt, stream: false, options: { num_predict: maxTokens } }
  );
  return (res.response || '').trim();
}

async function _huggingface(messages, cfg, maxTokens) {
  const key   = cfg.api_key;
  const model = cfg.model || 'HuggingFaceH4/zephyr-7b-beta';
  const prompt = messages.map(m => `<|${m.role}|>\n${m.content}`).join('\n') + '\n<|assistant|>';

  const headers = key ? { Authorization: `Bearer ${key}` } : {};
  const res = await _post(
    `https://api-inference.huggingface.co/models/${model}`,
    headers,
    { inputs: prompt, parameters: { max_new_tokens: maxTokens, temperature: 0.8, return_full_text: false } }
  );
  const text = Array.isArray(res) ? res[0]?.generated_text : res?.generated_text;
  if (!text) throw new Error('empty response');
  return text.trim();
}

// ---------------------------------------------------------------------------
// Static persona-aware fallback (no network required)
// ---------------------------------------------------------------------------

const _STATIC_RESPONSES = [
  'Thanks for chatting! Keep the energy up! 🔥',
  'Appreciate you being here! Drop a like if you\'re enjoying this!',
  'That\'s what I\'m talking about! Let\'s go!',
  'Love the support — you all are amazing!',
  'Stay tuned, more coming up!',
];
let _staticIdx = 0;

function _staticFallback(userMessage) {
  const persona = getPersona();
  let text = _STATIC_RESPONSES[_staticIdx % _STATIC_RESPONSES.length];
  _staticIdx++;
  if (persona?.style?.uppercase) text = text.toUpperCase();
  if (persona?.style?.prefix)    text = `${persona.style.prefix} ${text}`;
  if (persona?.style?.suffix)    text = `${text} ${persona.style.suffix}`;
  return text;
}

// ---------------------------------------------------------------------------
// Provider waterfall
// ---------------------------------------------------------------------------

const PROVIDERS = [
  { name: 'openai',      fn: _openai      },
  { name: 'anthropic',   fn: _anthropic   },
  { name: 'gemini',      fn: _gemini      },
  { name: 'groq',        fn: _groq        },
  { name: 'openrouter',  fn: _openrouter  },
  { name: 'ollama',      fn: _ollama      },
  { name: 'huggingface', fn: _huggingface },
];

let _llmConfig  = {};
let _lastProvider = null;

function configure(llmConfig) {
  _llmConfig = llmConfig || {};
}

/**
 * Call the LLM waterfall — returns a string response.
 * Falls back through all providers; always returns something.
 *
 * @param {string} userMessage
 * @param {{ user?: string, history?: Array }} [opts]
 * @returns {Promise<string>}
 */
async function chat(userMessage, opts = {}) {
  const maxTokens   = _llmConfig.max_tokens || 120;
  const systemText  = _llmConfig.system_prompt || _buildSystemPrompt();
  const providerCfg = _llmConfig.providers || {};

  const messages = [
    { role: 'system', content: systemText },
    ...(opts.history || []).slice(-6),          // last 3 turns context
    { role: 'user', content: userMessage },
  ];

  for (const { name, fn } of PROVIDERS) {
    const cfg = providerCfg[name] || {};

    // Skip if explicitly disabled
    if (cfg.enabled === false) continue;

    // Skip if key required but missing (non-local providers)
    if (name !== 'ollama' && !cfg.api_key) continue;

    try {
      const reply = await fn(messages, cfg, maxTokens);
      if (reply) {
        _lastProvider = name;
        appendEvent('llm:call', 'LLM_RESPONSE', { provider: name, user: opts.user, tokens: maxTokens });
        return reply;
      }
    } catch (e) {
      console.warn(`[llm] ${name} failed: ${e.message} — trying next`);
      appendEvent('llm:error', 'LLM_PROVIDER_FAILED', { provider: name, error: e.message });
    }
  }

  // Ultimate fallback — always works
  _lastProvider = 'static';
  appendEvent('llm:call', 'LLM_STATIC_FALLBACK', { user: opts.user });
  return _staticFallback(userMessage);
}

function getLastProvider() { return _lastProvider; }

function _buildSystemPrompt() {
  const persona = getPersona();
  return [
    `You are ${persona?.name || 'a live stream host'} on TikTok Live.`,
    persona?.mission || 'Keep the energy high, engage viewers, and drive conversions.',
    'Keep responses under 2 sentences. Be direct and hype. Never break character.',
  ].join(' ');
}

module.exports = { configure, chat, getLastProvider };
