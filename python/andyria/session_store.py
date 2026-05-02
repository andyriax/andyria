"""Session store with full-text search — mirrors hermes-agent's FTS5 session search.

Stores all conversation turns in SQLite at ``{data_dir}/sessions.db``.
A virtual FTS5 table enables full-text search across all past sessions.

Features:
    * Persistent sessions with titles
    * Full-text search via SQLite FTS5
    * /resume support — load a past session by ID or search snippet
    * Automatic title generation from the first user message

Usage::

    store = SessionStore(data_dir=Path("~/.andyria"))
    store.create("ses-abc", title="debugging the entropy beacon")
    store.append_turn("ses-abc", "user", "Why is entropy low?")
    store.append_turn("ses-abc", "assistant", "The beacon is offline.")
    results = store.search("entropy beacon")
    session = store.load("ses-abc")
"""

from __future__ import annotations

import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


@dataclass
class StoredTurn:
    turn_id: str
    session_id: str
    role: str
    content: str
    timestamp: float


@dataclass
class SessionSummary:
    session_id: str
    title: str
    turn_count: int
    created_at: float
    updated_at: float


@dataclass
class SearchResult:
    session_id: str
    session_title: str
    turn_id: str
    role: str
    snippet: str
    rank: float


class SessionStore:
    """SQLite-backed session store with FTS5 full-text search.

    Falls back gracefully if the current SQLite build lacks FTS5 (search
    will return an empty list with a warning rather than crashing).
    """

    def __init__(self, data_dir: Path) -> None:
        self._db_path = Path(data_dir) / "sessions.db"
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._has_fts5 = self._init_db()

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def create(self, session_id: Optional[str] = None, title: str = "") -> str:
        """Create a new session. Returns the session_id."""
        sid = session_id or str(uuid.uuid4())[:12]
        now = time.time()
        self._conn.execute(
            "INSERT OR IGNORE INTO sessions(id, title, created_at, updated_at) VALUES(?,?,?,?)",
            (sid, title or f"Session {sid}", now, now),
        )
        self._conn.commit()
        return sid

    def append_turn(self, session_id: str, role: str, content: str) -> str:
        """Append a turn and return its turn_id."""
        # Auto-create session if it doesn't exist
        self.create(session_id)
        turn_id = str(uuid.uuid4())[:12]
        now = time.time()
        self._conn.execute(
            "INSERT INTO turns(id, session_id, role, content, timestamp) VALUES(?,?,?,?,?)",
            (turn_id, session_id, role, content, now),
        )
        self._conn.execute(
            "UPDATE sessions SET updated_at=? WHERE id=?",
            (now, session_id),
        )
        # Auto-set title from first user message if not set
        if role == "user":
            row = self._conn.execute(
                "SELECT title FROM sessions WHERE id=?", (session_id,)
            ).fetchone()
            if row and (row["title"] == f"Session {session_id}" or not row["title"]):
                auto_title = content[:60].replace("\n", " ")
                self._conn.execute(
                    "UPDATE sessions SET title=? WHERE id=?",
                    (auto_title, session_id),
                )
        self._conn.commit()

        # Insert into FTS5 table
        if self._has_fts5:
            try:
                self._conn.execute(
                    "INSERT INTO turns_fts(turn_id, session_id, content) VALUES(?,?,?)",
                    (turn_id, session_id, content),
                )
                self._conn.commit()
            except sqlite3.OperationalError:
                pass

        return turn_id

    def load(self, session_id: str) -> Optional[tuple[SessionSummary, List[StoredTurn]]]:
        """Load a full session by ID."""
        row = self._conn.execute(
            "SELECT id, title, created_at, updated_at FROM sessions WHERE id=?",
            (session_id,),
        ).fetchone()
        if not row:
            return None
        turns_rows = self._conn.execute(
            "SELECT id, session_id, role, content, timestamp FROM turns "
            "WHERE session_id=? ORDER BY timestamp",
            (session_id,),
        ).fetchall()
        summary = SessionSummary(
            session_id=row["id"],
            title=row["title"],
            turn_count=len(turns_rows),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
        turns = [
            StoredTurn(
                turn_id=t["id"],
                session_id=t["session_id"],
                role=t["role"],
                content=t["content"],
                timestamp=t["timestamp"],
            )
            for t in turns_rows
        ]
        return summary, turns

    def list_sessions(self, limit: int = 20) -> List[SessionSummary]:
        """Return most-recent sessions."""
        rows = self._conn.execute(
            "SELECT s.id, s.title, s.created_at, s.updated_at, "
            "COUNT(t.id) AS turn_count "
            "FROM sessions s LEFT JOIN turns t ON t.session_id=s.id "
            "GROUP BY s.id ORDER BY s.updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            SessionSummary(
                session_id=r["id"],
                title=r["title"],
                turn_count=r["turn_count"],
                created_at=r["created_at"],
                updated_at=r["updated_at"],
            )
            for r in rows
        ]

    def delete_session(self, session_id: str) -> bool:
        c = self._conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
        self._conn.execute("DELETE FROM turns WHERE session_id=?", (session_id,))
        if self._has_fts5:
            try:
                self._conn.execute("DELETE FROM turns_fts WHERE session_id=?", (session_id,))
            except sqlite3.OperationalError:
                pass
        self._conn.commit()
        return c.rowcount > 0

    # ------------------------------------------------------------------
    # Full-text search
    # ------------------------------------------------------------------

    def search(self, query: str, limit: int = 10) -> List[SearchResult]:
        """FTS5 full-text search across all session turns."""
        if not self._has_fts5:
            return []
        try:
            rows = self._conn.execute(
                "SELECT f.turn_id, f.session_id, f.content, "
                "bm25(turns_fts) AS rank, s.title "
                "FROM turns_fts f "
                "JOIN sessions s ON s.id=f.session_id "
                "WHERE turns_fts MATCH ? "
                "ORDER BY rank LIMIT ?",
                (query, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            return []

        results = []
        for r in rows:
            snippet = r["content"][:120].replace("\n", " ")
            # Look up role
            turn = self._conn.execute(
                "SELECT role FROM turns WHERE id=?", (r["turn_id"],)
            ).fetchone()
            results.append(
                SearchResult(
                    session_id=r["session_id"],
                    session_title=r["title"],
                    turn_id=r["turn_id"],
                    role=turn["role"] if turn else "?",
                    snippet=snippet,
                    rank=float(r["rank"]),
                )
            )
        return results

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _init_db(self) -> bool:
        """Create tables; return True if FTS5 is available."""
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS sessions (
                id         TEXT PRIMARY KEY,
                title      TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )"""
        )
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS turns (
                id         TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                role       TEXT NOT NULL,
                content    TEXT NOT NULL,
                timestamp  REAL NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
            )"""
        )
        self._conn.commit()

        # Try to create FTS5 virtual table
        try:
            self._conn.execute(
                """CREATE VIRTUAL TABLE IF NOT EXISTS turns_fts
                   USING fts5(turn_id UNINDEXED, session_id UNINDEXED, content)"""
            )
            self._conn.commit()
            return True
        except sqlite3.OperationalError:
            return False
