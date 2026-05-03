/**
 * core/sleepscreen.js
 * Local HTTP server (port 3047) that serves the sleep screen UI.
 *
 * Endpoints:
 *   GET /           → sleep.html (full-screen ambient display)
 *   GET /weather    → proxies wttr.in free API (no key, IP-geolocation)
 *   GET /events     → Server-Sent Events stream for agent thoughts + status
 *
 * The server uses only Node built-ins (http, https, fs) — no extra packages.
 * Auto-opens the default browser when launched.
 */

'use strict';

const http   = require('http');
const https  = require('https');
const fs     = require('fs');
const path   = require('path');
const { exec } = require('child_process');

const PORT      = process.env.SLEEP_PORT || 3047;
const HTML_FILE = path.join(__dirname, '..', 'static', 'sleep.html');

let _server  = null;
let _clients = [];    // active SSE response streams
let _html    = null;  // cached HTML (re-read on each launch so edits take effect)

// ---------------------------------------------------------------------------
// SSE push — call this from sleeper.js after each use-case tick
// ---------------------------------------------------------------------------

/**
 * Push a typed event to all connected browser clients.
 * @param {string} type  - 'thought' | 'status' | 'wake' | 'openclaw' | 'jina'
 * @param {object} data
 */
function push(type, data) {
  const msg = `data: ${JSON.stringify({ type, data })}\n\n`;
  _clients = _clients.filter(res => {
    try { res.write(msg); return true; }
    catch (_) { return false; }
  });
}

// ---------------------------------------------------------------------------
// Weather proxy — wttr.in free JSON API, no key required
// ---------------------------------------------------------------------------

function _proxyWeather(res) {
  const req = https.get(
    'https://wttr.in/?format=j1',
    { headers: { 'User-Agent': 'curl/7.68.0', 'Accept': 'application/json' } },
    (r) => {
      let body = '';
      r.on('data', c => (body += c));
      r.on('end', () => {
        res.writeHead(200, {
          'Content-Type'                : 'application/json',
          'Access-Control-Allow-Origin' : '*',
          'Cache-Control'               : 'no-store',
        });
        res.end(body);
      });
    }
  );
  req.setTimeout(6000, () => {
    req.destroy();
    res.writeHead(503); res.end('{}');
  });
  req.on('error', () => {
    try { res.writeHead(503); res.end('{}'); } catch (_) {}
  });
}

// ---------------------------------------------------------------------------
// HTTP request handler
// ---------------------------------------------------------------------------

function _handler(req, res) {
  const url = req.url.split('?')[0];

  if (url === '/') {
    // Serve sleep screen — re-read each launch so UI edits are picked up
    try {
      const html = fs.readFileSync(HTML_FILE);
      res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
      res.end(html);
    } catch (e) {
      res.writeHead(500); res.end('Sleep screen not found: ' + e.message);
    }
    return;
  }

  if (url === '/weather') {
    _proxyWeather(res);
    return;
  }

  if (url === '/events') {
    // Server-Sent Events
    res.writeHead(200, {
      'Content-Type'                : 'text/event-stream',
      'Cache-Control'               : 'no-cache',
      'Connection'                  : 'keep-alive',
      'Access-Control-Allow-Origin' : '*',
    });
    res.write('retry: 3000\n\n');
    _clients.push(res);

    // Keepalive ping every 20s so browsers don't close the connection
    const ping = setInterval(() => {
      try { res.write(': ping\n\n'); }
      catch (_) { clearInterval(ping); }
    }, 20_000);

    req.on('close', () => {
      clearInterval(ping);
      _clients = _clients.filter(c => c !== res);
    });
    return;
  }

  res.writeHead(404); res.end();
}

// ---------------------------------------------------------------------------
// Browser launcher — works on Windows (wsl + native), macOS, Linux
// ---------------------------------------------------------------------------

function _openBrowser(url) {
  let cmd;
  if (process.platform === 'win32') {
    cmd = `start "" "${url}"`;
  } else if (process.platform === 'darwin') {
    cmd = `open "${url}"`;
  } else {
    // WSL or Linux: try several openers
    cmd = `xdg-open "${url}" 2>/dev/null || ` +
          `cmd.exe /c start "" "${url}" 2>/dev/null || ` +
          `sensible-browser "${url}" 2>/dev/null || true`;
  }
  exec(cmd, err => {
    if (err) console.warn('[sleepscreen] Browser auto-open failed — open manually:', url);
  });
}

// ---------------------------------------------------------------------------
// Lifecycle
// ---------------------------------------------------------------------------

/**
 * Start the local HTTP server and open the browser.
 * Idempotent — safe to call multiple times.
 */
function launch() {
  if (_server) return;

  _server = http.createServer(_handler);
  _server.listen(PORT, '127.0.0.1', () => {
    const url = `http://127.0.0.1:${PORT}`;
    console.log(`[sleepscreen] Sleep screen at ${url}`);
    _openBrowser(url);
  });

  _server.on('error', err => {
    if (err.code === 'EADDRINUSE') {
      console.warn(`[sleepscreen] Port ${PORT} in use — screen already open`);
      // Still push events to existing connections
    } else {
      console.error('[sleepscreen]', err.message);
    }
  });
}

/**
 * Push wake event (browser fades out) then close the server.
 */
function shutdown() {
  push('wake', {});
  // Give browser 1.5s to receive the wake event before closing socket
  setTimeout(() => {
    _clients.forEach(res => { try { res.end(); } catch (_) {} });
    _clients = [];
    if (_server) { _server.close(); _server = null; }
  }, 1500);
}

module.exports = { launch, push, shutdown };
