"""andyria.mcu_bridge — USB serial bridge for Arduino/ESP32 edge nodes.

Responsibilities
----------------
1. Auto-discover Andyria-compatible MCU devices on all serial ports.
2. Perform TOFU handshake (ESP32: HMAC-SHA256; Arduino: XOR echo).
3. Register each device as an Andyria agent via POST /v1/agents.
4. Stream entropy bytes from MCU into ``data/mcu_entropy.bin`` for
   the MCU entropy collector used by the beacon factory.
5. Forward heartbeats to POST /v1/mcu/heartbeat.
6. Reconnect automatically on disconnect.
7. Run silently; only logs at WARNING+ unless --verbose is set.

Usage (standalone)
------------------
    python -m andyria.mcu_bridge --auto --api http://localhost:7700
    python -m andyria.mcu_bridge --port /dev/ttyACM0 --api http://localhost:7700
    python -m andyria.mcu_bridge --port COM5 --api http://localhost:7700

Invocation as a library
-----------------------
    from andyria.mcu_bridge import McuBridge
    bridge = McuBridge(api_base="http://localhost:7700")
    await bridge.run()           # blocks; call cancel() to stop

Requirements: pyserial, httpx (already in requirements.txt)
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("andyria.mcu_bridge")

# ── Defaults ──────────────────────────────────────────────────────────────────
_DEFAULT_API = "http://localhost:7700"
_DEVICE_DB = Path.home() / ".andyria" / "data" / "mcu_devices.json"
_ENTROPY_Q = Path.home() / ".andyria" / "data" / "mcu_entropy.bin"
_BAUD = 115_200
_HANDSHAKE_TIMEOUT = 8.0  # seconds
_RECONNECT_DELAY = 5.0  # seconds


# ── Device registry (persisted as JSON) ──────────────────────────────────────
@dataclass
class McuDevice:
    node_id: str
    port: str
    firmware: str
    caps: List[str]
    device_key: Optional[str]  # hex; None for Arduino XOR-only devices
    agent_id: Optional[str]  # Andyria agent UUID assigned on first registration
    paired_at: float = field(default_factory=time.time)

    def has_hmac(self) -> bool:
        return "hmac_sha256" in self.caps and self.device_key is not None


def _load_db() -> Dict[str, McuDevice]:
    if not _DEVICE_DB.exists():
        return {}
    try:
        raw = json.loads(_DEVICE_DB.read_text())
        return {k: McuDevice(**v) for k, v in raw.items()}
    except Exception:
        return {}


def _save_db(db: Dict[str, McuDevice]) -> None:
    _DEVICE_DB.parent.mkdir(parents=True, exist_ok=True)
    tmp = _DEVICE_DB.with_suffix(".tmp")
    tmp.write_text(json.dumps({k: v.__dict__ for k, v in db.items()}, indent=2))
    tmp.replace(_DEVICE_DB)


# ── Serial helpers ────────────────────────────────────────────────────────────
def _list_candidate_ports() -> List[str]:
    """Return all USB serial port paths likely to host an MCU."""
    try:
        from serial.tools import list_ports  # type: ignore

        return [
            p.device
            for p in list_ports.comports()
            if p.vid is not None  # only USB-attached devices
            or any(
                kw in (p.description or "").lower()
                for kw in ("arduino", "esp32", "cp210", "ch340", "ftdi", "usb serial")
            )
        ]
    except ImportError:
        log.warning("pyserial not installed — cannot enumerate ports")
        return []


class _SerialConn:
    """Thin async wrapper around a pyserial Serial object."""

    def __init__(self, port: str, baud: int = _BAUD) -> None:
        import serial  # type: ignore  # deferred so import error is catchable

        self._s = serial.Serial(port, baud, timeout=1)

    def write_json(self, obj: Dict[str, Any]) -> None:
        line = json.dumps(obj, separators=(",", ":")) + "\n"
        self._s.write(line.encode())
        self._s.flush()

    def read_json(self, timeout: float = 2.0) -> Optional[Dict[str, Any]]:
        deadline = time.monotonic() + timeout
        buf = b""
        while time.monotonic() < deadline:
            chunk = self._s.readline()
            if chunk:
                buf += chunk
                try:
                    return json.loads(buf.decode("utf-8", errors="replace").strip())
                except json.JSONDecodeError:
                    buf = b""  # incomplete line; keep reading
        return None

    def read_json_nowait(self) -> Optional[Dict[str, Any]]:
        if self._s.in_waiting == 0:
            return None
        line = self._s.readline()
        if not line:
            return None
        try:
            return json.loads(line.decode("utf-8", errors="replace").strip())
        except json.JSONDecodeError:
            return None

    def close(self) -> None:
        try:
            self._s.close()
        except Exception:
            pass


# ── Attestation helpers ───────────────────────────────────────────────────────
def _verify_hmac(device_key_hex: str, nonce_hex: str, hmac_hex: str) -> bool:
    try:
        key = bytes.fromhex(device_key_hex)
        msg = bytes.fromhex(nonce_hex)
        given = bytes.fromhex(hmac_hex)
        expected = hmac.new(key, msg, hashlib.sha256).digest()
        return hmac.compare_digest(expected, given)
    except Exception:
        return False


def _verify_xor(node_id_hex: str, nonce_hex: str, response_hex: str) -> bool:
    """Verify Arduino XOR challenge-response."""
    try:
        node_id = bytes.fromhex(node_id_hex)
        nonce = bytes.fromhex(nonce_hex)
        given = bytes.fromhex(response_hex)
        expected = bytes(nonce[i] ^ node_id[i % len(node_id)] for i in range(len(nonce)))
        return hmac.compare_digest(expected, given[: len(expected)])
    except Exception:
        return False


# ── Per-device session ────────────────────────────────────────────────────────
class _DeviceSession:
    def __init__(self, port: str, conn: _SerialConn, device: McuDevice, api_client: Any) -> None:
        self.port = port
        self.conn = conn
        self.device = device
        self._client = api_client

    async def run_until_disconnect(self) -> None:
        log.info("Session active: node_id=%s port=%s", self.device.node_id, self.port)
        loop = asyncio.get_running_loop()
        while True:
            msg = await loop.run_in_executor(None, self.conn.read_json_nowait)
            if msg is None:
                await asyncio.sleep(0.05)
                continue

            mtype = msg.get("type")
            if mtype == "entropy":
                self._write_entropy(msg.get("bytes", ""))
            elif mtype == "heartbeat":
                await self._post_heartbeat(msg)
            elif mtype == "pong":
                pass  # latency check; discard
            elif mtype == "error":
                log.warning("MCU error from %s: %s", self.device.node_id, msg.get("msg"))

    def _write_entropy(self, hex_bytes: str) -> None:
        try:
            raw = bytes.fromhex(hex_bytes)
        except ValueError:
            return
        _ENTROPY_Q.parent.mkdir(parents=True, exist_ok=True)
        with open(_ENTROPY_Q, "ab") as fh:
            fh.write(raw)
        log.debug("Entropy written: %d bytes from %s", len(raw), self.device.node_id)

    async def _post_heartbeat(self, msg: Dict[str, Any]) -> None:
        payload = {
            "node_id": self.device.node_id,
            "agent_id": self.device.agent_id,
            "uptime_ms": msg.get("uptime_ms"),
            "free_heap": msg.get("free_heap"),
            "rssi": msg.get("rssi"),
            "ts": time.time(),
        }
        try:
            import httpx  # type: ignore

            async with httpx.AsyncClient(timeout=4.0) as c:
                await c.post(f"{self._client}/v1/mcu/heartbeat", json=payload)
        except Exception as exc:
            log.debug("Heartbeat post failed: %s", exc)


# ── Handshake ─────────────────────────────────────────────────────────────────
async def _handshake(port: str, db: Dict[str, McuDevice], api_base: str) -> Optional[_DeviceSession]:
    loop = asyncio.get_running_loop()
    try:
        conn = await loop.run_in_executor(None, lambda: _SerialConn(port))
    except Exception as exc:
        log.debug("Cannot open %s: %s", port, exc)
        return None

    # Wait for ready banner (device may be mid-reboot)
    nonce = secrets.token_bytes(32)
    nonce_hex = nonce.hex()

    def _do_handshake() -> Optional[Dict[str, Any]]:
        # Drain any stale bytes
        time.sleep(0.5)
        conn._s.reset_input_buffer()
        conn.write_json({"cmd": "hello", "nonce": nonce_hex})
        return conn.read_json(timeout=_HANDSHAKE_TIMEOUT)

    ident = await loop.run_in_executor(None, _do_handshake)
    if not ident or ident.get("type") != "ident":
        log.debug("No ident from %s (not an Andyria MCU)", port)
        conn.close()
        return None

    node_id = ident.get("node_id", "")
    firmware = ident.get("firmware", "unknown")
    caps = ident.get("caps") or []
    key_export = ident.get("key_export")  # non-null on first boot only
    hmac_hex = ident.get("hmac", "")

    if not node_id:
        conn.close()
        return None

    # ── Lookup or create device record ────────────────────────────────────────
    if node_id in db:
        device = db[node_id]
        # Verify attestation
        if device.has_hmac():
            if not _verify_hmac(device.device_key, nonce_hex, hmac_hex):
                log.warning("HMAC attestation FAILED for %s on %s — rejecting", node_id, port)
                conn.close()
                return None
            log.info("HMAC attestation passed for %s", node_id)
        else:
            # Arduino XOR
            if not _verify_xor(node_id, nonce_hex, hmac_hex):
                log.warning("XOR response mismatch for %s on %s", node_id, port)
                conn.close()
                return None
            log.info("XOR response verified for %s (basic attestation)", node_id)
        device.port = port
    else:
        # First-time device — TOFU registration
        device_key: Optional[str] = None
        if "hmac_sha256" in caps:
            if not key_export:
                log.warning("ESP32 device %s did not export key on first boot — rejecting", node_id)
                conn.close()
                return None
            # Verify the key is consistent with the HMAC it sent
            if not _verify_hmac(key_export, nonce_hex, hmac_hex):
                log.warning("First-boot HMAC verification failed for %s — rejecting", node_id)
                conn.close()
                return None
            device_key = key_export
            log.info("First-boot TOFU: paired ESP32 node_id=%s", node_id)
        else:
            if not _verify_xor(node_id, nonce_hex, hmac_hex):
                log.warning("First-boot XOR failed for %s", node_id)
                conn.close()
                return None
            log.info("First-boot TOFU: paired Arduino node_id=%s (basic)", node_id)

        # Register as Andyria agent
        agent_id = await _register_agent(node_id, firmware, caps, api_base)
        device = McuDevice(
            node_id=node_id,
            port=port,
            firmware=firmware,
            caps=caps,
            device_key=device_key,
            agent_id=agent_id,
        )
        db[node_id] = device
        _save_db(db)

        # Tell device it is now paired (suppresses future key_export)
        def _ack() -> None:
            conn.write_json({"cmd": "paired_ack"})

        await loop.run_in_executor(None, _ack)

    return _DeviceSession(port, conn, device, api_base)


async def _register_agent(node_id: str, firmware: str, caps: List[str], api_base: str) -> Optional[str]:
    """POST /v1/agents to register the MCU as an Andyria agent."""
    payload = {
        "name": f"mcu-{node_id[:8]}",
        "model": "stub",
        "system_prompt": (
            f"You are an Andyria MCU edge node ({firmware}). "
            f"Capabilities: {', '.join(caps)}. "
            "You contribute physical entropy to the DAG and report hardware telemetry."
        ),
        "persona": "edge_node",
        "skills": ["entropy", "telemetry"],
    }
    try:
        import httpx  # type: ignore

        async with httpx.AsyncClient(timeout=6.0) as c:
            r = await c.post(f"{api_base}/v1/agents", json=payload)
            if r.status_code == 201:
                agent_id = r.json().get("id")
                log.info("Registered MCU %s as agent %s", node_id, agent_id)
                return agent_id
    except Exception as exc:
        log.warning("Agent registration failed for %s: %s", node_id, exc)
    return None


# ── Main bridge class ─────────────────────────────────────────────────────────
class McuBridge:
    """Manages all active MCU device sessions.

    Parameters
    ----------
    api_base:
        Andyria HTTP API base URL (e.g. ``http://localhost:7700``).
    ports:
        Explicit list of serial ports. If empty, auto-discovery is used.
    scan_interval:
        Seconds between port scans for newly connected devices.
    """

    def __init__(
        self,
        api_base: str = _DEFAULT_API,
        ports: Optional[List[str]] = None,
        scan_interval: float = 15.0,
    ) -> None:
        self._api_base = api_base
        self._explicit_ports = ports or []
        self._scan_interval = scan_interval
        self._db = _load_db()
        self._sessions: Dict[str, asyncio.Task[None]] = {}
        self._stop = asyncio.Event()

    def cancel(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        log.info("MCU bridge starting — api=%s", self._api_base)
        _ENTROPY_Q.parent.mkdir(parents=True, exist_ok=True)

        while not self._stop.is_set():
            ports = self._explicit_ports if self._explicit_ports else _list_candidate_ports()
            for port in ports:
                if port not in self._sessions or self._sessions[port].done():
                    session = await _handshake(port, self._db, self._api_base)
                    if session:
                        task = asyncio.create_task(
                            self._run_session(session),
                            name=f"mcu-{session.device.node_id[:8]}",
                        )
                        self._sessions[port] = task

            # Clean up finished sessions
            self._sessions = {p: t for p, t in self._sessions.items() if not t.done()}

            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._scan_interval)
            except asyncio.TimeoutError:
                pass

        for t in self._sessions.values():
            t.cancel()
        log.info("MCU bridge stopped")

    async def _run_session(self, session: _DeviceSession) -> None:
        try:
            await session.run_until_disconnect()
        except Exception as exc:
            log.info("Session for %s ended: %s — will reconnect", session.port, exc)
        finally:
            session.conn.close()


# ── CLI entry point ───────────────────────────────────────────────────────────
async def _main(api: str, ports: List[str], verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )
    bridge = McuBridge(api_base=api, ports=ports)
    try:
        await bridge.run()
    except KeyboardInterrupt:
        bridge.cancel()


def main() -> None:
    import argparse

    p = argparse.ArgumentParser(description="Andyria MCU bridge daemon")
    p.add_argument("--api", default=_DEFAULT_API, help="Andyria API base URL")
    p.add_argument(
        "--port",
        action="append",
        default=[],
        metavar="PORT",
        help="Serial port (repeat for multiple); omit for auto-discover",
    )
    p.add_argument("--auto", action="store_true", help="Auto-discover ports (default if --port omitted)")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()
    asyncio.run(_main(args.api, args.port, args.verbose))


if __name__ == "__main__":
    main()
