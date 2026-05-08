"""GistStore — GitHub Gist-backed distributed memory for Andyria.

Treats GitHub Gists as permanent, publicly-addressable blockchain records:

  - Each node owns one "ledger gist" containing its event chain snapshot
    (NDJSON) and a labelled-chains index (JSON).
  - Any peer node may register as a **mirror**: it pulls the gist, re-posts
    a forked/local copy, and earns JETS reward credits for doing so.
  - The mirror registry itself is stored in the gist's ``mirrors.json`` file
    so the record is self-contained and survives node restarts.

Usage::

    store = GistStore(token="ghp_...", node_id="node-abc")
    gist_id = await store.push(events, labelled_chains)
    events   = await store.pull(gist_id)
    await store.register_mirror("node-xyz", gist_id="xyz-gist-id")
    credits  = store.get_rewards("node-xyz")

Environment variables (fallbacks for the token)::

    ANDYRIA_GITHUB_TOKEN   — preferred
    GITHUB_TOKEN           — CI/CD fallback
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

import httpx

from .models import Event

logger = logging.getLogger(__name__)

_GIST_API = "https://api.github.com/gists"
_MIRROR_REWARD_PER_SYNC = 5  # JETS credits per successful mirror sync
_MAX_REWARD_ACCUMULATE = 1000  # cap per node so no runaway inflation


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _token_from_env() -> Optional[str]:
    return os.environ.get("ANDYRIA_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")


def _auth_headers(token: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _events_to_ndjson(events: List[Event]) -> str:
    return "\n".join(e.model_dump_json() for e in events) + "\n" if events else ""


def _ndjson_to_events(text: str) -> List[Event]:
    out: List[Event] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(Event.model_validate_json(line))
        except Exception:
            pass
    return out


# ---------------------------------------------------------------------------
# Core class
# ---------------------------------------------------------------------------


class GistStore:
    """GitHub Gist-backed distributed ledger mirror with mirror rewards.

    Args:
        token: GitHub personal access token with ``gist`` scope.  If omitted
               the constructor falls back to env vars.
        node_id: This node's stable identifier (written into gist filenames
                 so multiple nodes' gists are distinguishable).
        public: Whether the ledger gist is public (default ``True`` so any
                peer can pull without auth).
    """

    def __init__(
        self,
        node_id: str,
        token: Optional[str] = None,
        public: bool = True,
    ) -> None:
        self.node_id = node_id
        self._token = token or _token_from_env()
        self._public = public
        # In-memory mirror registry: node_id → {gist_id, last_sync_ns, credits}
        self._mirrors: Dict[str, Dict[str, Any]] = {}
        # Own gist id (set after first push)
        self._own_gist_id: Optional[str] = None

    # ------------------------------------------------------------------
    # Push — write local state to own gist
    # ------------------------------------------------------------------

    async def push(
        self,
        events: List[Event],
        labelled_chains: Optional[List[Dict[str, Any]]] = None,
    ) -> Optional[str]:
        """Serialize events + labels and upsert to this node's ledger gist.

        Returns the gist ID on success, or ``None`` if no token is configured
        or the API call fails (errors are logged, never raised).
        """
        if not self._token:
            logger.debug("GistStore: no GitHub token — push skipped")
            return None

        ledger_file = f"andyria-ledger-{self.node_id}.ndjson"
        labels_file = f"andyria-labels-{self.node_id}.json"
        mirrors_file = f"andyria-mirrors-{self.node_id}.json"

        files: Dict[str, Any] = {
            ledger_file: {"content": _events_to_ndjson(events) or "# empty\n"},
            labels_file: {"content": json.dumps(labelled_chains or [], indent=2)},
            mirrors_file: {"content": json.dumps(self._mirrors, indent=2)},
        }

        description = (
            f"Andyria node {self.node_id} — event ledger + chain labels "
            f"[{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}]"
        )

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                if self._own_gist_id:
                    resp = await client.patch(
                        f"{_GIST_API}/{self._own_gist_id}",
                        headers=_auth_headers(self._token),
                        json={"description": description, "files": files},
                    )
                else:
                    resp = await client.post(
                        _GIST_API,
                        headers=_auth_headers(self._token),
                        json={
                            "description": description,
                            "public": self._public,
                            "files": files,
                        },
                    )
                resp.raise_for_status()
                data = resp.json()
                self._own_gist_id = data["id"]
                logger.info("GistStore: pushed %d events to gist %s", len(events), self._own_gist_id)
                return self._own_gist_id
        except Exception as exc:
            logger.warning("GistStore.push failed: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Pull — fetch events from any gist
    # ------------------------------------------------------------------

    async def pull(
        self,
        gist_id: str,
        node_id_hint: Optional[str] = None,
    ) -> List[Event]:
        """Fetch and deserialize events from a remote node's ledger gist.

        ``node_id_hint`` is used to construct the expected filename; if omitted
        all ``.ndjson`` files in the gist are attempted.
        """
        headers = _auth_headers(self._token) if self._token else {"Accept": "application/vnd.github+json"}
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(f"{_GIST_API}/{gist_id}", headers=headers)
                resp.raise_for_status()
                data = resp.json()

            all_events: List[Event] = []
            for fname, fobj in data.get("files", {}).items():
                if not fname.endswith(".ndjson"):
                    continue
                if node_id_hint and node_id_hint not in fname:
                    continue
                raw_url = fobj.get("raw_url")
                if not raw_url:
                    content = fobj.get("content", "")
                else:
                    async with httpx.AsyncClient(timeout=15.0) as client:
                        cr = await client.get(raw_url)
                        content = cr.text
                all_events.extend(_ndjson_to_events(content))

            logger.info("GistStore: pulled %d events from gist %s", len(all_events), gist_id)
            return all_events
        except Exception as exc:
            logger.warning("GistStore.pull(%s) failed: %s", gist_id, exc)
            return []

    # ------------------------------------------------------------------
    # Pull labelled chains index from a remote gist
    # ------------------------------------------------------------------

    async def pull_labels(
        self,
        gist_id: str,
        node_id_hint: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch the labels index from a remote gist."""
        headers = _auth_headers(self._token) if self._token else {"Accept": "application/vnd.github+json"}
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(f"{_GIST_API}/{gist_id}", headers=headers)
                resp.raise_for_status()
                data = resp.json()

            for fname, fobj in data.get("files", {}).items():
                if not fname.endswith(".json") or "labels" not in fname:
                    continue
                if node_id_hint and node_id_hint not in fname:
                    continue
                raw_url = fobj.get("raw_url")
                content = fobj.get("content", "")
                if raw_url:
                    async with httpx.AsyncClient(timeout=15.0) as client:
                        cr = await client.get(raw_url)
                        content = cr.text
                return json.loads(content)
        except Exception as exc:
            logger.warning("GistStore.pull_labels(%s) failed: %s", gist_id, exc)
        return []

    # ------------------------------------------------------------------
    # Mirror registry + rewards
    # ------------------------------------------------------------------

    def register_mirror(self, mirror_node_id: str, gist_id: str) -> None:
        """Register a peer node as a mirror for a specific gist.

        Mirrors earn JETS credits each time they sync (call ``record_mirror_sync``).
        """
        if mirror_node_id not in self._mirrors:
            self._mirrors[mirror_node_id] = {
                "gist_id": gist_id,
                "credits": 0,
                "sync_count": 0,
                "last_sync_ns": 0,
                "registered_at_ns": time.time_ns(),
            }
            logger.info("GistStore: registered mirror node %s → gist %s", mirror_node_id, gist_id)
        else:
            # Update gist_id if they have a newer one
            self._mirrors[mirror_node_id]["gist_id"] = gist_id

    def record_mirror_sync(self, mirror_node_id: str) -> int:
        """Award JETS credits to a mirror node after a verified sync.

        Returns the node's updated credit balance.
        """
        if mirror_node_id not in self._mirrors:
            logger.debug("GistStore: unknown mirror %s — skipping reward", mirror_node_id)
            return 0
        entry = self._mirrors[mirror_node_id]
        new_balance = min(
            entry["credits"] + _MIRROR_REWARD_PER_SYNC,
            _MAX_REWARD_ACCUMULATE,
        )
        entry["credits"] = new_balance
        entry["sync_count"] += 1
        entry["last_sync_ns"] = time.time_ns()
        logger.info(
            "GistStore: mirror %s sync reward +%d → balance %d",
            mirror_node_id,
            _MIRROR_REWARD_PER_SYNC,
            new_balance,
        )
        return new_balance

    def get_rewards(self, mirror_node_id: str) -> int:
        """Return accumulated JETS credit balance for a mirror node."""
        return self._mirrors.get(mirror_node_id, {}).get("credits", 0)

    def list_mirrors(self) -> List[Dict[str, Any]]:
        """Return all registered mirrors with their stats."""
        return [{"node_id": nid, **info} for nid, info in self._mirrors.items()]

    # ------------------------------------------------------------------
    # Convenience: sync all known mirrors by pulling their gists
    # ------------------------------------------------------------------

    async def sync_mirrors(self) -> Dict[str, List[Event]]:
        """Pull events from all registered mirror gists.

        Rewards each mirror that provides at least one event.  Returns a
        dict mapping mirror_node_id → list of new events pulled.
        """
        results: Dict[str, List[Event]] = {}
        for node_id, info in list(self._mirrors.items()):
            gist_id = info.get("gist_id")
            if not gist_id:
                continue
            events = await self.pull(gist_id, node_id_hint=node_id)
            results[node_id] = events
            if events:
                self.record_mirror_sync(node_id)
        return results

    # ------------------------------------------------------------------
    # Own gist URL helper
    # ------------------------------------------------------------------

    @property
    def own_gist_url(self) -> Optional[str]:
        if self._own_gist_id:
            return f"https://gist.github.com/{self._own_gist_id}"
        return None

    def load_mirror_state(self, raw: Dict[str, Any]) -> None:
        """Restore mirror registry from a previously serialised dict (e.g.
        pulled back from the mirrors.json file in this node's own gist)."""
        self._mirrors.update(raw)
