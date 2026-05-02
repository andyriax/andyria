"""Mesh networking for Andyria — peer discovery and event gossip."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from .store import EventStore


class PeerStatus:
    """Runtime status of a single peer."""

    def __init__(self, url: str) -> None:
        self.url = url
        self.node_id: str | None = None
        self.last_seen_ns = 0
        self.events_synced = 0
        self.reachable = False


class MeshManager:
    """Manages peer discovery and event gossip via pull-based protocol."""

    def __init__(
        self,
        peer_urls: list[str],
        store: EventStore,
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

    async def start(self) -> None:
        """Start the background gossip loop."""
        self._running = True
        self._task = asyncio.create_task(self._gossip_loop())

    async def stop(self) -> None:
        """Stop the gossip loop and wait for it to finish."""
        self._running = False
        if self._task:
            await self._task

    async def _gossip_loop(self) -> None:
        """Background task that periodically syncs events from all peers."""
        while self._running:
            tasks = [self._gossip_with_peer(url) for url in self.peer_urls]
            await asyncio.gather(*tasks, return_exceptions=True)
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
                        # Skip malformed events
                        pass

                status.reachable = True
                status.last_seen_ns = now_ns
                status.events_synced = synced_count

        except Exception:
            status.reachable = False

    def add_peer(self, url: str) -> None:
        """Add a new peer at runtime. Gossip with it starts on next cycle."""
        if url not in self.peer_urls:
            self.peer_urls.append(url)
            self.peer_status[url] = PeerStatus(url)

    def get_peer_statuses(self) -> dict[str, PeerStatus]:
        """Return current status of all peers."""
        return dict(self.peer_status)
