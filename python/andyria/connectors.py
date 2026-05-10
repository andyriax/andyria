"""Third-party service connectors for Andyria.

Connectors mirror signed control events to external systems such as Discord
webhooks, Discord bot channels, or generic HTTP endpoints. They are
intentionally best-effort: the core coordinator never blocks on connector
failures.
"""

from __future__ import annotations

import json
import os
import threading
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import (
    ConnectorCreateRequest,
    ConnectorDefinition,
    ConnectorKind,
    ConnectorSyncRequest,
    ConnectorSyncResult,
    ConnectorUpdateRequest,
    Event,
)


class ConnectorRegistry:
    """Persistent registry for external service connectors."""

    def __init__(self, data_dir: Path) -> None:
        self._path = Path(data_dir) / "connectors.json"
        self._lock = threading.RLock()
        self._definitions: Dict[str, ConnectorDefinition] = {}
        self._load()
        self._load_from_env()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(payload, list):
            return
        with self._lock:
            for item in payload:
                if not isinstance(item, dict):
                    continue
                try:
                    definition = ConnectorDefinition.model_validate(item)
                except Exception:
                    continue
                self._definitions[definition.connector_id] = definition

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = [definition.model_dump(mode="json") for definition in self.list()]
        self._path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def _load_from_env(self) -> None:
        raw = os.environ.get("ANDYRIA_CONNECTORS_JSON", "").strip()
        if raw:
            try:
                payload = json.loads(raw)
            except Exception:
                payload = []
            if isinstance(payload, list):
                for item in payload:
                    if isinstance(item, dict):
                        try:
                            self.create(ConnectorCreateRequest.model_validate(item))
                        except Exception:
                            pass

        discord_webhook_url = os.environ.get("ANDYRIA_DISCORD_WEBHOOK_URL", "").strip()
        if discord_webhook_url and not any(defn.kind == ConnectorKind.DISCORD for defn in self.list()):
            self.create(
                ConnectorCreateRequest(
                    name="Discord",
                    kind=ConnectorKind.DISCORD,
                    config={"url": discord_webhook_url, "username": os.environ.get("ANDYRIA_DISCORD_USERNAME", "Andyria")},
                )
            )

        discord_bot_token = os.environ.get("ANDYRIA_DISCORD_BOT_TOKEN", "").strip()
        discord_channel_id = os.environ.get("ANDYRIA_DISCORD_CHANNEL_ID", "").strip()
        if discord_bot_token and discord_channel_id and not any(defn.kind == ConnectorKind.DISCORD_BOT for defn in self.list()):
            self.create(
                ConnectorCreateRequest(
                    name="Discord Bot",
                    kind=ConnectorKind.DISCORD_BOT,
                    config={
                        "token": discord_bot_token,
                        "channel_id": discord_channel_id,
                        "username": os.environ.get("ANDYRIA_DISCORD_BOT_NAME", "Andyria"),
                    },
                )
            )

    def list(self) -> List[ConnectorDefinition]:
        with self._lock:
            return sorted(self._definitions.values(), key=lambda item: item.name.lower())

    def get(self, connector_id: str) -> Optional[ConnectorDefinition]:
        with self._lock:
            return self._definitions.get(connector_id)

    def create(self, request: ConnectorCreateRequest) -> ConnectorDefinition:
        connector_id = f"connector-{uuid.uuid4().hex[:12]}"
        definition = ConnectorDefinition(
            connector_id=connector_id,
            name=request.name.strip(),
            kind=request.kind,
            enabled=request.enabled,
            config=dict(request.config),
        )
        with self._lock:
            self._definitions[connector_id] = definition
            self._save()
        return definition

    def update(self, connector_id: str, request: ConnectorUpdateRequest) -> Optional[ConnectorDefinition]:
        with self._lock:
            current = self._definitions.get(connector_id)
            if current is None:
                return None
            updated = current.model_copy(
                update={
                    "name": request.name.strip() if request.name is not None else current.name,
                    "kind": request.kind if request.kind is not None else current.kind,
                    "config": dict(request.config) if request.config is not None else dict(current.config),
                    "enabled": request.enabled if request.enabled is not None else current.enabled,
                }
            )
            self._definitions[connector_id] = updated
            self._save()
            return updated

    def delete(self, connector_id: str) -> bool:
        with self._lock:
            removed = self._definitions.pop(connector_id, None)
            if removed is None:
                return False
            self._save()
            return True

    def sync_connector(
        self,
        connector_id: str,
        request: Optional[ConnectorSyncRequest] = None,
        event: Optional[Event] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ConnectorSyncResult:
        definition = self.get(connector_id)
        if definition is None:
            return ConnectorSyncResult(connector_id=connector_id, ok=False, status="not_found", detail="Connector not found")
        if not definition.enabled:
            return ConnectorSyncResult(connector_id=connector_id, ok=False, status="disabled", detail="Connector is disabled")

        try:
            if definition.kind == ConnectorKind.DISCORD:
                detail = self._post_discord(definition, request=request, event=event, metadata=metadata)
            elif definition.kind == ConnectorKind.DISCORD_BOT:
                detail = self._post_discord_bot(definition, request=request, event=event, metadata=metadata)
            else:
                detail = self._post_webhook(definition, request=request, event=event, metadata=metadata)
            self._mark_synced(definition.connector_id)
            return ConnectorSyncResult(connector_id=connector_id, ok=True, status="sent", detail=detail)
        except Exception as exc:
            self._mark_error(definition.connector_id, str(exc))
            return ConnectorSyncResult(connector_id=connector_id, ok=False, status="error", detail=str(exc))

    def dispatch_event(self, event: Event, metadata: Dict[str, Any]) -> None:
        """Best-effort asynchronous fan-out for committed events."""
        for definition in self.list():
            if not definition.enabled:
                continue
            thread = threading.Thread(
                target=self.sync_connector,
                kwargs={"connector_id": definition.connector_id, "event": event, "metadata": metadata},
                daemon=True,
            )
            thread.start()

    def _mark_synced(self, connector_id: str) -> None:
        with self._lock:
            current = self._definitions.get(connector_id)
            if current is None:
                return
            self._definitions[connector_id] = current.model_copy(
                update={"last_synced_ns": int(__import__("time").time_ns()), "last_error": None}
            )
            self._save()

    def _mark_error(self, connector_id: str, detail: str) -> None:
        with self._lock:
            current = self._definitions.get(connector_id)
            if current is None:
                return
            self._definitions[connector_id] = current.model_copy(update={"last_error": detail})
            self._save()

    def _post_webhook(
        self,
        definition: ConnectorDefinition,
        request: Optional[ConnectorSyncRequest],
        event: Optional[Event],
        metadata: Optional[Dict[str, Any]],
    ) -> str:
        url = str(definition.config.get("url", "")).strip()
        if not url:
            raise ValueError("Connector config requires a url")

        payload: Dict[str, Any] = {
            "connector": definition.model_dump(mode="json"),
            "request": request.model_dump() if request else None,
            "event": event.model_dump(mode="json") if event else None,
            "metadata": metadata or {},
        }
        if request and request.message:
            payload["message"] = request.message

        self._post_json(url, payload, headers=definition.config.get("headers"))
        return f"posted webhook to {url}"

    def _post_discord(
        self,
        definition: ConnectorDefinition,
        request: Optional[ConnectorSyncRequest],
        event: Optional[Event],
        metadata: Optional[Dict[str, Any]],
    ) -> str:
        url = str(definition.config.get("url", "")).strip()
        if not url:
            raise ValueError("Discord connector config requires a webhook url")

        content = request.message if request else "Andyria event sync"
        if event is not None:
            content = f"{content}\n**{event.event_type.value}** `{event.id}`"
        payload: Dict[str, Any] = {
            "username": definition.config.get("username", "Andyria"),
            "content": content,
            "allowed_mentions": {"parse": []},
            "embeds": [
                {
                    "title": definition.name,
                    "description": event.event_type.value if event is not None else (request.message if request else "manual sync"),
                    "color": 0x5865F2,
                    "fields": [
                        {"name": "connector_id", "value": definition.connector_id, "inline": True},
                        {"name": "kind", "value": definition.kind.value, "inline": True},
                    ],
                }
            ],
            "metadata": metadata or {},
        }
        self._post_json(url, payload)
        return f"posted discord webhook to {url}"

    def _post_discord_bot(
        self,
        definition: ConnectorDefinition,
        request: Optional[ConnectorSyncRequest],
        event: Optional[Event],
        metadata: Optional[Dict[str, Any]],
    ) -> str:
        channel_id = str(definition.config.get("channel_id", "")).strip()
        token = str(definition.config.get("token", "")).strip()
        api_base_url = str(definition.config.get("api_base_url", "https://discord.com/api/v10")).rstrip("/")
        if not channel_id:
            raise ValueError("Discord bot connector config requires a channel_id")
        if not token:
            raise ValueError("Discord bot connector config requires a token")

        content = request.message if request else "Andyria event sync"
        if event is not None:
            content = f"{content}\n**{event.event_type.value}** `{event.id}`"

        payload: Dict[str, Any] = {
            "content": content,
            "allowed_mentions": {"parse": []},
        }
        if metadata:
            payload["metadata"] = metadata

        self._post_json(
            f"{api_base_url}/channels/{channel_id}/messages",
            payload,
            headers={
                "Authorization": f"Bot {token}",
                "User-Agent": definition.config.get("user_agent", "Andyria/1.0"),
            },
        )
        return f"posted discord bot message to channel {channel_id}"

    def _post_json(self, url: str, payload: Dict[str, Any], headers: Optional[Dict[str, Any]] = None) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        for key, value in (headers or {}).items():
            req.add_header(str(key), str(value))
        timeout_s = float(os.environ.get("ANDYRIA_CONNECTOR_TIMEOUT_S", "5"))
        with urllib.request.urlopen(req, timeout=timeout_s) as response:
            response.read()