"""Entropy Beacon factory for Andyria.

Combines physical entropy sources, XOR-mixes them, whitens with
SHA3-256 (with BLAKE3 if available), and produces a signed
``EntropyBeacon`` record that can be committed to the event log.

Design invariant
----------------
Event content hashes include ``beacon.id`` — not the raw entropy bytes.
This keeps hashes deterministic and independently verifiable by any
peer, while still anchoring every event to physical-world randomness
at the originating node.
"""

from __future__ import annotations

import hashlib
import json
import struct
import time
from typing import List, Optional

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from ..models import EntropyBeacon
from .collectors import EntropySource, build_collector_chain
from .health import EntropyHealthMonitor


def _content_hash(data: bytes) -> str:
    """Hash with BLAKE3 when available, else SHA3-256. Returns hex string."""
    try:
        import blake3  # type: ignore
        return blake3.blake3(data).hexdigest()
    except ImportError:
        return hashlib.sha3_256(data).hexdigest()


def _derive_bytes(data: bytes, label: bytes, length: int) -> bytes:
    """Derive ``length`` bytes keyed from ``data`` and a domain ``label``."""
    try:
        import blake3  # type: ignore
        return blake3.blake3(data + label).digest(length=length)
    except ImportError:
        return hashlib.shake_256(data + label).digest(length)


def _canonical_beacon(beacon: EntropyBeacon) -> bytes:
    """Stable, deterministic JSON serialization for signing."""
    return json.dumps(
        {
            "id": beacon.id,
            "timestamp_ns": beacon.timestamp_ns,
            "sources": beacon.sources,
            "raw_entropy_hash": beacon.raw_entropy_hash,
            "nonce": beacon.nonce,
            "node_id": beacon.node_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


class EntropyBeaconFactory:
    """Generates signed ``EntropyBeacon`` records from physical sources.

    Parameters
    ----------
    node_id:
        Unique identifier for this node (included in every beacon).
    private_key:
        Ed25519 private key used to sign each beacon.
    sources:
        Optional allowlist of source names. Defaults to all available.
    """

    def __init__(
        self,
        node_id: str,
        private_key: Ed25519PrivateKey,
        sources: Optional[List[str]] = None,
    ) -> None:
        self._node_id = node_id
        self._private_key = private_key
        self._collectors: List[EntropySource] = build_collector_chain(sources)
        self._health: dict[str, EntropyHealthMonitor] = {
            c.name: EntropyHealthMonitor() for c in self._collectors
        }

    @property
    def source_names(self) -> List[str]:
        return [c.name for c in self._collectors]

    def generate(self, nonce_bytes: int = 32) -> EntropyBeacon:
        """Collect physical entropy and produce a signed beacon.

        Returned beacon fields
        ----------------------
        id               BLAKE3/SHA3 hash of (raw_entropy_hash + nonce + timestamp + node_id)
        raw_entropy_hash BLAKE3/SHA3 of all collected + mixed + timestamp-bound bytes
        nonce            ``nonce_bytes`` of whitened entropy; usable as a salt / IV
        signature        Ed25519 over canonical JSON (id excluded from raw hash to avoid
                         circular dependency; id is derived from the hash)
        """
        timestamp_ns = time.perf_counter_ns()

        # 1. Collect from all available sources
        raw_parts: List[bytes] = []
        active_sources: List[str] = []

        for collector in self._collectors:
            try:
                raw = collector.collect(num_bytes=64)
                if raw:
                    failures = self._health[collector.name].update(raw)
                    # Log health failures but do not abort — degraded entropy is
                    # better than no entropy; the mix step combines all sources.
                    for f in failures:
                        if not f.passed:
                            import logging
                            _log = logging.getLogger(__name__)
                            # clock_jitter APT failures are expected on WSL/virtual
                            # environments where perf_counter_ns has low resolution.
                            # Log at DEBUG to avoid flooding production logs.
                            _level = (
                                logging.DEBUG
                                if collector.name == "clock_jitter" and f.test_name == "APT"
                                else logging.WARNING
                            )
                            _log.log(
                                _level,
                                "Entropy health check %s failed for %s: %s",
                                f.test_name, collector.name, f.detail,
                            )
                    raw_parts.append(raw)
                    active_sources.append(collector.name)
            except Exception:
                continue  # Degrade gracefully; os_urandom is always last resort

        if not raw_parts:
            raise RuntimeError("All entropy sources failed — cannot generate beacon")

        # 2. XOR-mix all parts (longest length wins, shorter parts wrap)
        max_len = max(len(p) for p in raw_parts)
        mixed = bytearray(max_len)
        for part in raw_parts:
            for i, b in enumerate(part):
                mixed[i % max_len] ^= b

        # 3. Bind to timestamp + node_id to prevent cross-node/cross-time replay
        combined = bytes(mixed) + struct.pack(">Q", timestamp_ns) + self._node_id.encode()

        # 4. Whiten
        raw_entropy_hash = _content_hash(combined)

        # 5. Derive nonce (domain-separated so it is independent of the hash)
        nonce_raw = _derive_bytes(combined, b"\x00andyria-nonce", nonce_bytes)
        nonce_hex = nonce_raw.hex()

        # 6. Derive beacon ID from the whitened hash (not from raw bytes)
        id_input = json.dumps(
            {
                "raw_entropy_hash": raw_entropy_hash,
                "nonce": nonce_hex,
                "timestamp_ns": timestamp_ns,
                "node_id": self._node_id,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        beacon_id = _content_hash(id_input)

        # 7. Build + sign
        beacon = EntropyBeacon(
            id=beacon_id,
            timestamp_ns=timestamp_ns,
            sources=active_sources,
            raw_entropy_hash=raw_entropy_hash,
            nonce=nonce_hex,
            node_id=self._node_id,
            signature="",
        )
        canonical = _canonical_beacon(beacon)
        sig = self._private_key.sign(canonical)
        beacon.signature = sig.hex()

        return beacon

    def verify(self, beacon: EntropyBeacon, public_key: Ed25519PublicKey) -> bool:
        """Verify the Ed25519 signature on a beacon."""
        try:
            canonical = _canonical_beacon(beacon)
            sig_bytes = bytes.fromhex(beacon.signature)
            public_key.verify(sig_bytes, canonical)
            return True
        except Exception:
            return False
