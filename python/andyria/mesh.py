"""Mesh networking for Andyria — peer discovery, event gossip, auto-learning,
copy-homework, machine dreams, and mesh growth health."""

from __future__ import annotations

import asyncio
import collections
import logging
import time
from typing import TYPE_CHECKING, Any, Callable, Deque, Optional

import httpx

if TYPE_CHECKING:
    from .store import EventStore

logger = logging.getLogger(__name__)

_DREAMS_MAX = 100        # ring-buffer size for local dreams
_LEARNED_PREFIX = "[learned] "


class PeerStatus:
    """Runtime status of a single peer."""

    def __init__(self, url: str) -> None:
        self.url = url
        self.node_id: str | None = None
        self.last_seen_ns = 0
        self.events_synced = 0
        self.reachable = False


# ---------------------------------------------------------------------------
# Mesh growth health
# ---------------------------------------------------------------------------

class MeshGrowthHealth:
    """Tracks peer topology over time and computes growth / reachability health.

    Call :meth:`snapshot` after each gossip round.  Read results via
    :meth:`report`.
    """

    _WINDOW_SECONDS = 3600      # 1-hour observation window for growth rate

    def __init__(self, node_id: str) -> None:
        self._node_id = node_id
        # Deque of (timestamp_ns, total_peers, reachable_peers)
        self._history: Deque[tuple[int, int, int]] = collections.deque(maxlen=720)  # ~2h at 10s

    def snapshot(self, total_peers: int, reachable_peers: int) -> None:
        self._history.append((time.time_ns(), total_peers, reachable_peers))

    def report(self) -> dict[str, Any]:
        """Return a growth-health dict matching MeshGrowthReport schema."""
        from .models import MeshGrowthReport, MeshGrowthSnapshot
        snaps = [
            MeshGrowthSnapshot(timestamp_ns=ts, total_peers=t, reachable_peers=r,
                               unreachable_peers=max(0, t - r))
            for ts, t, r in self._history
        ]
        current_total = self._history[-1][1] if self._history else 0
        current_reach = self._history[-1][2] if self._history else 0
        reachability_pct = (current_reach / current_total * 100.0) if current_total > 0 else 100.0

        growth_rate = 0.0
        window_ns = self._WINDOW_SECONDS * 1_000_000_000
        now_ns = time.time_ns()
        old = [s for s in self._history if now_ns - s[0] >= window_ns]
        if old:
            oldest_total = old[0][1]
            growth_rate = (current_total - oldest_total) / (self._WINDOW_SECONDS / 3600.0)

        warnings: list[str] = []
        if current_total > 0 and reachability_pct < 50.0:
            warnings.append(f"Low reachability: {reachability_pct:.1f}% of peers unreachable")
        if growth_rate < -2:
            warnings.append(f"Mesh shrinking at {abs(growth_rate):.1f} peers/hour")

        report = MeshGrowthReport(
            node_id=self._node_id,
            snapshots=snaps[-60:],       # last 60 samples in response
            current_peers=current_total,
            reachable_now=current_reach,
            reachability_pct=round(reachability_pct, 2),
            growth_rate_per_hour=round(growth_rate, 4),
            healthy=len(warnings) == 0,
            warnings=warnings,
        )
        return report.model_dump()


# ---------------------------------------------------------------------------
# MeshManager
# ---------------------------------------------------------------------------

class MeshManager:
    """Manages peer discovery, event gossip, auto-learning via mesh,
    copy-homework (promptbook/chain sharing), machine dreams, and growth health.
    """

    def __init__(
        self,
        peer_urls: list[str],
        store: "EventStore",
        node_id: str,
        gossip_interval_ms: int = 10_000,
    ) -> None:
        """
        Initialize MeshManager.

        Args:
            peer_urls: List of peer HTTP URLs (e.g. "http://peer:7700")
            store: Local EventStore for appending replicated events
            node_id: This node's ID (used in logging/status)
            gossip_interval_ms: How often to poll peers (default 10s)
        """
        self.peer_urls = peer_urls
        self.store = store
        self.node_id = node_id
        self.gossip_interval_ms = gossip_interval_ms

        self.peer_status: dict[str, PeerStatus] = {url: PeerStatus(url) for url in peer_urls}
        self._task: asyncio.Task | None = None
        self._running = False

        # Machine dreams ring-buffer
        self._dreams: Deque[dict[str, Any]] = collections.deque(maxlen=_DREAMS_MAX)

        # Callbacks set by coordinator
        self._emit_event: Optional[Callable[[str, dict, Any], None]] = None
        self._ingest_learned: Optional[Callable[[str, float], bool]] = None  # (pattern, conf) → bool

        # Growth health monitor
        self._growth = MeshGrowthHealth(node_id)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the background gossip loop."""
        self._running = True
        self._task = asyncio.create_task(self._gossip_loop())

    async def stop(self) -> None:
        """Stop the gossip loop and wait for it to finish."""
        self._running = False
        if self._task:
            await self._task

    # ------------------------------------------------------------------
    # Callbacks wired by coordinator
    # ------------------------------------------------------------------

    def set_emit_event(self, fn: Callable[[str, dict, Any], None]) -> None:
        self._emit_event = fn

    def set_ingest_learned(self, fn: Callable[[str, float], bool]) -> None:
        """Provide a callback that ingests one mesh-sourced learned pattern.

        The callable receives ``(pattern_text, confidence)`` and returns True
        if the pattern was stored.
        """
        self._ingest_learned = fn

    # ------------------------------------------------------------------
    # Machine dreams
    # ------------------------------------------------------------------

    def add_dream(self, thought: str, confidence: float = 0.0, tags: list[str] | None = None) -> dict:
        """Record an ATM output as a machine dream for mesh sharing."""
        from .models import MachineDream
        dream = MachineDream(
            origin_node_id=self.node_id,
            thought=thought,
            confidence=confidence,
            tags=tags or [],
        )
        entry = dream.model_dump()
        self._dreams.append(entry)
        if self._emit_event:
            try:
                self._emit_event("MESH_DREAM_ADDED", {"dream_id": dream.id, "preview": thought[:80]}, None)
            except Exception:
                pass
        return entry

    def get_dreams(self, limit: int = 20) -> list[dict]:
        """Return recent machine dreams (newest last), up to *limit*."""
        items = list(self._dreams)
        return items[-limit:]

    # ------------------------------------------------------------------
    # Gossip loop
    # ------------------------------------------------------------------

    async def _gossip_loop(self) -> None:
        """Background task that periodically syncs events from all peers."""
        while self._running:
            tasks = [self._gossip_with_peer(url) for url in list(self.peer_urls)]
            await asyncio.gather(*tasks, return_exceptions=True)

            # Post-round growth snapshot
            total = len(self.peer_urls)
            reachable = sum(1 for s in self.peer_status.values() if s.reachable)
            self._growth.snapshot(total, reachable)

            await asyncio.sleep(self.gossip_interval_ms / 1000.0)

    async def _gossip_with_peer(self, peer_url: str) -> None:
        """Pull events from a single peer and append them locally."""
        status = self.peer_status[peer_url]
        now_ns = time.time_ns()

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                # Fetch status to get node_id
                status_res = await client.get(f"{peer_url}/v1/status")
                status_res.raise_for_status()
                status_data = status_res.json()
                status.node_id = status_data.get("node_id")

                # Fetch events and append any new ones
                events_res = await client.get(f"{peer_url}/v1/events")
                events_res.raise_for_status()
                events_data = events_res.json()

                synced_count = 0
                for event_dict in events_data:
                    from .models import Event

                    try:
                        event = Event(**event_dict)
                        if self.store.append(event):
                            synced_count += 1
                    except (TypeError, ValueError):
                        pass

                status.reachable = True
                status.last_seen_ns = now_ns
                status.events_synced = synced_count

        except Exception:
            status.reachable = False

    # ------------------------------------------------------------------
    # Auto-learning via mesh
    # ------------------------------------------------------------------

    async def sync_learned_from_peers(self) -> dict[str, int]:
        """Pull ``[learned]`` patterns from all reachable peers and ingest them.

        Returns a mapping of peer_url → patterns_absorbed.
        """
        results: dict[str, int] = {}
        tasks = [
            (url, asyncio.create_task(self._pull_learned_from_peer(url)))
            for url, status in self.peer_status.items()
            if status.reachable
        ]
        for url, task in tasks:
            try:
                count = await task
                results[url] = count
            except Exception:
                results[url] = 0
        return results

    async def _pull_learned_from_peer(self, peer_url: str) -> int:
        """Fetch MEMORY file from *peer_url*, extract [learned] lines, ingest."""
        if self._ingest_learned is None:
            return 0
        absorbed = 0
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                r = await client.get(f"{peer_url}/v1/memory/MEMORY")
                r.raise_for_status()
                data = r.json()
                content: str = data.get("content", "")
                for line in content.splitlines():
                    line = line.strip()
                    if not line.startswith(_LEARNED_PREFIX):
                        continue
                    # Strip metadata suffix e.g. "  [src=mesh, conf=0.85]"
                    pattern = line[len(_LEARNED_PREFIX):]
                    # Extract confidence if present
                    conf = 0.82
                    import re
                    m = re.search(r"conf=([\d.]+)", pattern)
                    if m:
                        try:
                            conf = float(m.group(1))
                        except ValueError:
                            pass
                        pattern = pattern[:m.start()].rstrip()
                    if self._ingest_learned(pattern, conf):
                        absorbed += 1
        except Exception as exc:
            logger.debug("mesh learn pull from %s failed: %s", peer_url, exc)
        if absorbed and self._emit_event:
            try:
                self._emit_event("MESH_LEARNED", {"peer": peer_url, "absorbed": absorbed}, None)
            except Exception:
                pass
        return absorbed

    # ------------------------------------------------------------------
    # Copy-homework (promptbook + chain sharing)
    # ------------------------------------------------------------------

    async def copy_homework_from_peers(self) -> list[dict]:
        """Pull promptbooks and chains from all reachable peers.

        Returns a list of HomeworkItem dicts ready for the coordinator to import.
        """
        all_items: list[dict] = []
        tasks = [
            asyncio.create_task(self._pull_homework_from_peer(url))
            for url, status in self.peer_status.items()
            if status.reachable
        ]
        for coro in asyncio.as_completed(tasks):
            try:
                items = await coro
                all_items.extend(items)
            except Exception:
                pass
        return all_items

    async def _pull_homework_from_peer(self, peer_url: str) -> list[dict]:
        """Fetch promptbooks and chains from one peer."""
        from .models import HomeworkItem
        items: list[dict] = []
        async with httpx.AsyncClient(timeout=8.0) as client:
            # promptbooks
            try:
                r = await client.get(f"{peer_url}/v1/promptbooks")
                r.raise_for_status()
                for pb in r.json():
                    hw = HomeworkItem(
                        peer_url=peer_url,
                        kind="promptbook",
                        id=pb.get("id", ""),
                        name=pb.get("name", ""),
                        body=pb,
                    )
                    items.append(hw.model_dump())
            except Exception:
                pass
            # chains
            try:
                r = await client.get(f"{peer_url}/v1/chains")
                r.raise_for_status()
                for ch in r.json():
                    hw = HomeworkItem(
                        peer_url=peer_url,
                        kind="chain",
                        id=ch.get("id", ""),
                        name=ch.get("name", ""),
                        body=ch,
                    )
                    items.append(hw.model_dump())
            except Exception:
                pass
        if items and self._emit_event:
            try:
                self._emit_event("MESH_HOMEWORK_COPIED",
                                 {"peer": peer_url, "count": len(items)}, None)
            except Exception:
                pass
        return items

    # ------------------------------------------------------------------
    # Dream sharing across mesh
    # ------------------------------------------------------------------

    async def sync_dreams_from_peers(self) -> int:
        """Pull machine dreams from all reachable peers into local ring-buffer.

        Returns total new dreams absorbed.
        """
        absorbed = 0
        local_ids = {d["id"] for d in self._dreams}
        tasks = [
            asyncio.create_task(self._pull_dreams_from_peer(url, local_ids))
            for url, status in self.peer_status.items()
            if status.reachable
        ]
        for coro in asyncio.as_completed(tasks):
            try:
                new = await coro
                absorbed += new
            except Exception:
                pass
        if absorbed and self._emit_event:
            try:
                self._emit_event("MESH_DREAM_SYNCED", {"absorbed": absorbed}, None)
            except Exception:
                pass
        return absorbed

    async def _pull_dreams_from_peer(self, peer_url: str, known_ids: set[str]) -> int:
        """Fetch /v1/dreams from one peer and append novel ones locally."""
        absorbed = 0
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(f"{peer_url}/v1/dreams", params={"limit": 20})
                r.raise_for_status()
                for dream in r.json():
                    dream_id = dream.get("id", "")
                    if dream_id and dream_id not in known_ids:
                        self._dreams.append(dream)
                        known_ids.add(dream_id)
                        absorbed += 1
        except Exception as exc:
            logger.debug("mesh dreams pull from %s failed: %s", peer_url, exc)
        return absorbed

    # ------------------------------------------------------------------
    # Growth health
    # ------------------------------------------------------------------

    def growth_report(self) -> dict[str, Any]:
        """Return the current mesh growth health report."""
        return self._growth.report()

    # ------------------------------------------------------------------
    # Peer management
    # ------------------------------------------------------------------

    def add_peer(self, url: str) -> None:
        """Add a new peer at runtime. Gossip with it starts on next cycle."""
        if url not in self.peer_urls:
            self.peer_urls.append(url)
            self.peer_status[url] = PeerStatus(url)

    def get_peer_statuses(self) -> dict[str, PeerStatus]:
        """Return current status of all peers."""
        return dict(self.peer_status)
