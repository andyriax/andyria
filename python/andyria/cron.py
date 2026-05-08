"""Built-in cron scheduler for Andyria — mirrors hermes-agent's scheduled automations.

Jobs are stored in ``{data_dir}/cron.db`` (SQLite). A background thread
polls every 30 seconds and delivers output back to the session via an
optional push callback.

Natural-language schedule parsing is handled by a simple pattern matcher
so no external library is required. Standard cron expressions (5-field)
are also accepted.

Usage::

    scheduler = CronScheduler(data_dir=Path("~/.andyria"))
    scheduler.set_push(callback)   # fn(job_id, output_text) → None
    scheduler.start()

    job_id = scheduler.add("daily-standup", "every day at 09:00", "summarise overnight changes")
    scheduler.list()
    scheduler.cancel(job_id)
    scheduler.stop()
"""

from __future__ import annotations

import re
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

_POLL_INTERVAL = 30  # seconds between scheduler ticks
_NATURAL_PATTERNS: list[tuple[re.Pattern, str]] = [
    # "every N minutes/hours"
    (re.compile(r"every\s+(\d+)\s+minute", re.I), "interval_minutes"),
    (re.compile(r"every\s+(\d+)\s+hour", re.I), "interval_hours"),
    # "every minute" / "every hour"
    (re.compile(r"every\s+minute", re.I), "every_minute"),
    (re.compile(r"every\s+hour", re.I), "every_hour"),
    (re.compile(r"every\s+day\s+at\s+(\d{1,2}):(\d{2})", re.I), "daily_hhmm"),
    (re.compile(r"every\s+day\s+at\s+(\d{1,2})(am|pm)", re.I), "daily_ampm"),
    (re.compile(r"daily\s+at\s+(\d{1,2}):(\d{2})", re.I), "daily_hhmm"),
]


def _parse_schedule(expression: str) -> dict:
    """Parse a natural-language or cron expression into a schedule dict.

    Returns a dict with keys understood by ``_is_due()``.
    """
    expr = expression.strip()

    # 5-field cron: min hour dom month dow
    parts = expr.split()
    if len(parts) == 5:
        return {"cron": expr}

    for pattern, kind in _NATURAL_PATTERNS:
        m = pattern.search(expr)
        if not m:
            continue
        if kind == "interval_minutes":
            return {"interval_seconds": int(m.group(1)) * 60}
        if kind == "interval_hours":
            return {"interval_seconds": int(m.group(1)) * 3600}
        if kind == "every_minute":
            return {"interval_seconds": 60}
        if kind == "every_hour":
            return {"interval_seconds": 3600}
        if kind == "daily_hhmm":
            return {"daily": True, "hour": int(m.group(1)), "minute": int(m.group(2))}
        if kind == "daily_ampm":
            h = int(m.group(1))
            if m.group(2).lower() == "pm" and h != 12:
                h += 12
            if m.group(2).lower() == "am" and h == 12:
                h = 0
            return {"daily": True, "hour": h, "minute": 0}

    # Fallback: treat as interval in seconds if numeric
    if expr.isdigit():
        return {"interval_seconds": int(expr)}

    # Unknown — run once per hour as a safe default
    return {"interval_seconds": 3600, "expression": expr}


def _is_due(schedule: dict, last_run: float, now: float) -> bool:
    """Return True if the job should fire now."""
    if "interval_seconds" in schedule:
        return (now - last_run) >= schedule["interval_seconds"]
    if schedule.get("daily"):
        import datetime

        dt = datetime.datetime.fromtimestamp(now)
        if dt.hour == schedule["hour"] and dt.minute == schedule["minute"]:
            last_dt = datetime.datetime.fromtimestamp(last_run)
            # Only fire once per minute
            return (last_dt.date() < dt.date()) or (last_dt.hour != dt.hour) or (last_dt.minute != dt.minute)
    if "cron" in schedule:
        # Basic cron: only support minute/hour fields (1 and 2)
        fields = schedule["cron"].split()
        import datetime

        dt = datetime.datetime.fromtimestamp(now)
        minute_ok = fields[0] == "*" or int(fields[0]) == dt.minute
        hour_ok = fields[1] == "*" or int(fields[1]) == dt.hour
        if minute_ok and hour_ok:
            last_dt = datetime.datetime.fromtimestamp(last_run)
            return not (last_dt.hour == dt.hour and last_dt.minute == dt.minute)
    return False


@dataclass
class CronJob:
    id: str
    name: str
    expression: str
    task: str
    platform: str
    last_run: float
    next_run: float
    active: bool = True
    created_at: float = 0.0


class CronScheduler:
    """Persistent background cron scheduler backed by SQLite.

    Args:
        data_dir:     Directory for cron.db.
        executor:     Optional async-capable function ``(task: str) → str``.
                      Called when a job fires; return value is forwarded to
                      the push callback.
        push:         Optional callback ``(job_id: str, output: str) → None``.
    """

    def __init__(
        self,
        data_dir: Path,
        executor: Optional[Callable[[str], str]] = None,
        push: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        self._db_path = Path(data_dir) / "cron.db"
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        self._executor = executor
        self._push = push
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_push(self, fn: Callable[[str, str], None]) -> None:
        self._push = fn

    def set_executor(self, fn: Callable[[str], str]) -> None:
        self._executor = fn

    def add(self, name: str, expression: str, task: str, platform: str = "andyria") -> str:
        """Add a new cron job. Returns the job ID."""
        job_id = str(uuid.uuid4())[:8]
        now = time.time()
        _parse_schedule(expression)  # validate expression; raises if invalid
        c = self._conn.cursor()
        c.execute(
            "INSERT INTO jobs(id,name,expression,task,platform,last_run,next_run,active,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (job_id, name, expression, task, platform, 0.0, now, 1, now),
        )
        self._conn.commit()
        return job_id

    def cancel(self, job_id: str) -> bool:
        """Deactivate a job. Returns True if found."""
        c = self._conn.cursor()
        c.execute("UPDATE jobs SET active=0 WHERE id=?", (job_id,))
        self._conn.commit()
        return c.rowcount > 0

    def delete(self, job_id: str) -> bool:
        """Permanently delete a job."""
        c = self._conn.cursor()
        c.execute("DELETE FROM jobs WHERE id=?", (job_id,))
        self._conn.commit()
        return c.rowcount > 0

    def list(self, include_inactive: bool = False) -> List[CronJob]:
        """Return list of cron jobs."""
        c = self._conn.cursor()
        if include_inactive:
            c.execute("SELECT * FROM jobs ORDER BY created_at")
        else:
            c.execute("SELECT * FROM jobs WHERE active=1 ORDER BY created_at")
        return [CronJob(*row) for row in c.fetchall()]

    def get(self, job_id: str) -> Optional[CronJob]:
        c = self._conn.cursor()
        c.execute("SELECT * FROM jobs WHERE id=?", (job_id,))
        row = c.fetchone()
        return CronJob(*row) if row else None

    def start(self) -> None:
        """Start the background scheduler thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="andyria-cron")
        self._thread.start()

    def stop(self) -> None:
        """Stop the background scheduler thread gracefully."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS jobs (
                id          TEXT PRIMARY KEY,
                name        TEXT NOT NULL,
                expression  TEXT NOT NULL,
                task        TEXT NOT NULL,
                platform    TEXT NOT NULL DEFAULT 'andyria',
                last_run    REAL NOT NULL DEFAULT 0,
                next_run    REAL NOT NULL DEFAULT 0,
                active      INTEGER NOT NULL DEFAULT 1,
                created_at  REAL NOT NULL DEFAULT 0
            )"""
        )
        self._conn.commit()

    def _loop(self) -> None:
        while not self._stop_event.wait(timeout=_POLL_INTERVAL):
            self._tick()

    def _tick(self) -> None:
        now = time.time()
        c = self._conn.cursor()
        c.execute("SELECT * FROM jobs WHERE active=1")
        for row in c.fetchall():
            job = CronJob(*row)
            schedule = _parse_schedule(job.expression)
            if not _is_due(schedule, job.last_run, now):
                continue
            # Fire the job
            output = self._run_job(job)
            c.execute("UPDATE jobs SET last_run=? WHERE id=?", (now, job.id))
            self._conn.commit()
            if self._push and output:
                try:
                    self._push(job.id, output)
                except Exception:
                    pass

    def _run_job(self, job: CronJob) -> str:
        if self._executor:
            try:
                return self._executor(job.task)
            except Exception as exc:
                return f"[cron error] {exc}"
        return f"[cron] Job '{job.name}' fired: {job.task}"

    # ------------------------------------------------------------------
    # Self-wake helpers
    # ------------------------------------------------------------------

    _SELF_WAKE_TASK_PREFIX = "__self_wake__"

    def schedule_self_wake(
        self,
        expression: str = "every 30 minutes",
        name: str = "self-wake",
        on_wake: Optional[Callable[[], None]] = None,
    ) -> str:
        """Register a recurring self-wake job.

        When fired the job invokes *on_wake* (if provided) and then the
        normal push callback so callers can treat it as a regular event.

        Returns the new job ID.
        """
        task_payload = f"{self._SELF_WAKE_TASK_PREFIX}{name}"
        if on_wake is not None:
            # Wrap executor to call on_wake before normal execution
            _orig_exec = self._executor

            def _wake_executor(task: str) -> str:
                if task.startswith(self._SELF_WAKE_TASK_PREFIX):
                    try:
                        on_wake()
                    except Exception:
                        pass
                    return f"[self-wake] {name} activated"
                return _orig_exec(task) if _orig_exec else f"[cron] {task}"

            self._executor = _wake_executor

        return self.add(name=name, expression=expression, task=task_payload, platform="andyria")

    def is_self_wake_task(self, task: str) -> bool:
        """Return True if *task* was created by :meth:`schedule_self_wake`."""
        return task.startswith(self._SELF_WAKE_TASK_PREFIX)
