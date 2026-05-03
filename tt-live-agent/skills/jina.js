'use strict';

const { appendEvent } = require('../core/db');
const { format } = require('../core/persona');
const { listPersonas } = require('../core/persona');
const { listSkills } = require('../core/skillLoader');
const { listAgents } = require('../core/orchestrator');
const sleepscreen = require('../core/sleepscreen');

const EVENT_COOLDOWN_MS = {
  member_joined: 45_000,
  gift_received: 20_000,
  stream_shared: 30_000,
};

const _lastEventAt = new Map();

function _frameworkActions() {
  return [
    'Map the event flow and DAG transitions before adding new behavior.',
    'Create focused skills with clear command contracts and telemetry events.',
    'Split autonomous agents by trigger type to avoid noisy cross-talk.',
    'Use persona + voice profiles to keep role clarity across agents.',
    'Instrument host/system events so assistants can suggest next actions live.',
  ];
}

function _eventAdvice(eventName, data) {
  const user = data.user || 'viewer';
  const coins = Number(data.coins || 0);

  switch (eventName) {
    case 'agent_started':
      return {
        title: 'Jina online',
        detail: 'Baseline checks passed. Next: confirm skills, personas, and event routes in one dry-run pass.',
      };
    case 'live_connected':
      return {
        title: 'Live connection stable',
        detail: 'Run a quick command smoke test: /help, /skill list, /jina framework to validate runtime wiring.',
      };
    case 'member_joined':
      return {
        title: 'Audience growth signal',
        detail: `Welcome ${user}, then route to a relevant capability path to convert attention into session depth.`,
      };
    case 'gift_received':
      return {
        title: 'Revenue intent detected',
        detail: `${user} sent a gift (${coins} coins). Trigger a value-forward CTA and track conversion events.`,
      };
    case 'stream_shared':
      return {
        title: 'Distribution lift',
        detail: `Share from ${user} means discovery momentum. Rotate to high-clarity framework demos for new viewers.`,
      };
    case 'sleep_entered':
      return {
        title: 'Low-traffic mode',
        detail: 'Use this window to run use-case discovery and generate the next best skill candidates.',
      };
    case 'sleep_exited':
      return {
        title: 'Active mode resumed',
        detail: 'Re-prioritize fast interactions: greet, orient, and provide one concrete action path.',
      };
    case 'tiktok_error':
    case 'connect_error':
      return {
        title: 'Transport fault detected',
        detail: 'Escalate recovery: verify username/config, connector health, and recent auth/network changes.',
      };
    case 'live_disconnected':
      return {
        title: 'Session disconnected',
        detail: 'Queue reconnect diagnostics and preserve recent event context for deterministic replay.',
      };
    case 'agent_stopping':
      return {
        title: 'Controlled shutdown',
        detail: 'Persist final state snapshots and event counts before process exit.',
      };
    default:
      return {
        title: 'Architect guidance',
        detail: 'Track intent, evidence, and outcomes on each event so the system can self-improve safely.',
      };
  }
}

function _buildAudit() {
  const agents = listAgents();
  const skills = listSkills();
  const personas = listPersonas();

  const skillSet = new Set(skills.map(s => s.command));
  const triggerSet = new Set();
  let inactiveCount = 0;
  let errorStateCount = 0;

  agents.forEach(agent => {
    (agent.triggers || []).forEach(t => triggerSet.add(t));
    if (agent.active === false) inactiveCount += 1;
    if (agent.state === 'ERROR') errorStateCount += 1;
  });

  const checks = [
    {
      label: 'Host/system coverage',
      ok: triggerSet.has('host') && triggerSet.has('system'),
      fix: 'Add an autonomous assistant agent with host/system triggers.',
    },
    {
      label: 'Architect command availability',
      ok: skillSet.has('/jina'),
      fix: 'Ensure /jina skill is loaded and exposed to chat commander.',
    },
    {
      label: 'Command router baseline',
      ok: skillSet.has('/help') && skillSet.has('/skill') && skillSet.has('/dag'),
      fix: 'Expose /help, /skill, and /dag for on-stream operations.',
    },
    {
      label: 'Persona breadth',
      ok: personas.length >= 3,
      fix: 'Add more personas for role clarity and tonal separation.',
    },
    {
      label: 'Runtime stability',
      ok: errorStateCount === 0,
      fix: 'Reset or repair agents currently in ERROR state.',
    },
  ];

  const passCount = checks.filter(c => c.ok).length;
  const score = `${passCount}/${checks.length}`;
  const critical = checks.filter(c => !c.ok).map(c => c.fix);

  const lines = [
    `Framework audit score: ${score}`,
    `Agents: ${agents.length} (inactive: ${inactiveCount}, error: ${errorStateCount})`,
    `Skills: ${skills.length}`,
    `Personas: ${personas.length}`,
  ];
  checks.forEach(c => {
    lines.push(`${c.ok ? 'PASS' : 'FAIL'} · ${c.label}`);
  });

  return {
    score,
    checks,
    summary: lines.join('\n'),
    next: critical.slice(0, 3),
    stats: {
      agents: agents.length,
      inactive: inactiveCount,
      errors: errorStateCount,
      skills: skills.length,
      personas: personas.length,
    },
  };
}

function _shouldEmit(eventName, severity) {
  if (severity === 'critical') return true;
  const cooldown = EVENT_COOLDOWN_MS[eventName] || 15_000;
  const now = Date.now();
  const last = _lastEventAt.get(eventName) || 0;
  if (now - last < cooldown) return false;
  _lastEventAt.set(eventName, now);
  return true;
}

module.exports = {
  command: '/jina',
  description: 'Architect assistant: framework guidance and host/system event attunement',

  execute(args, ctx) {
    const mode = (args[0] || '').toLowerCase();
    const eventName = ctx.data?.event || mode;
    const severity = ctx.data?.severity || 'info';
    const user = ctx.user || 'viewer';

    if (mode === 'audit') {
      const audit = _buildAudit();
      const title = `Jina framework audit ${audit.score}`;
      ctx.speak(`${title}. Top line: ${audit.checks.filter(c => !c.ok).length === 0 ? 'all core checks passed' : 'remediation advised for failed checks'}.`);
      sleepscreen.push('jina', {
        title,
        detail: audit.summary,
        severity: audit.checks.every(c => c.ok) ? 'info' : 'high',
        actions: audit.next,
      });
      appendEvent('skill:jina', 'JINA_AUDIT', {
        user,
        score: audit.score,
        stats: audit.stats,
        failed_checks: audit.checks.filter(c => !c.ok).map(c => c.label),
      });
      return;
    }

    if (!eventName || eventName === 'framework') {
      const actions = _frameworkActions();
      const spoken = format(
        'Jina architecture brief for {user}: {a1} {a2} {a3}',
        {
          user,
          a1: actions[0],
          a2: actions[1],
          a3: actions[2],
        }
      );
      ctx.speak(spoken);
      sleepscreen.push('jina', {
        title: 'Framework action map',
        detail: actions.join(' '),
        severity: 'info',
        actions,
      });
      appendEvent('skill:jina', 'JINA_FRAMEWORK_GUIDANCE', { user, actions });
      return;
    }

    if (!_shouldEmit(eventName, severity)) return;

    const advice = _eventAdvice(eventName, ctx.data || {});
    const payload = {
      event: eventName,
      severity,
      user,
      title: advice.title,
      detail: advice.detail,
      actions: _frameworkActions(),
    };

    sleepscreen.push('jina', payload);
    appendEvent('skill:jina', 'JINA_EVENT_GUIDANCE', payload);

    if (severity === 'critical') {
      ctx.speak(format('{title}. {detail}', advice));
    }
  },
};
