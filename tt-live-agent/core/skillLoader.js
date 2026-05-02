/**
 * core/skillLoader.js
 * Hot-reload skill registry — BotFather-style /command dispatch.
 *
 * Skills are CommonJS modules in ./skills/ with the shape:
 * {
 *   command: "/greet",
 *   description: "Greet a joining viewer",
 *   execute(args, ctx) { ... }
 * }
 *
 * Skills are loaded on startup and whenever a file in ./skills/ changes
 * (via chokidar watch — handled in agent.js).
 */

'use strict';

const fs   = require('fs');
const path = require('path');

const SKILLS_DIR = path.join(__dirname, '..', 'skills');

let _skills = {};

// ---------------------------------------------------------------------------
// Load / reload
// ---------------------------------------------------------------------------

function loadSkills() {
  _skills = {};

  const files = fs.readdirSync(SKILLS_DIR).filter(f => f.endsWith('.js'));

  for (const file of files) {
    const fullPath = path.join(SKILLS_DIR, file);

    // Bust require cache so hot-reload picks up changes
    delete require.cache[require.resolve(fullPath)];

    try {
      const skill = require(fullPath);
      if (!skill.command) {
        console.warn(`[skills] ${file} missing .command — skipped`);
        continue;
      }
      _skills[skill.command] = skill;
    } catch (e) {
      console.error(`[skills] Failed to load ${file}:`, e.message);
    }
  }

  console.log(`[skills] Loaded: ${Object.keys(_skills).join(', ') || '(none)'}`);
  return Object.keys(_skills);
}

function getSkill(cmd) {
  return _skills[cmd] || null;
}

function listSkills() {
  return Object.values(_skills).map(s => ({
    command    : s.command,
    description: s.description || '',
  }));
}

module.exports = { loadSkills, getSkill, listSkills };
