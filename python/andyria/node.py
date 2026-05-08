"""
Andyria node identity and Ed25519 key management.

Keys are persisted to disk (PEM, mode 0o600). On first run a fresh
Ed25519 keypair is generated; on subsequent runs the existing key is
loaded.

Upgrade path
------------
When ML-DSA (CRYSTALS-Dilithium / FIPS 204) keys are production-ready,
introduce a ``PostQuantumKeyPair`` wrapper that implements the same
``sign`` / ``public_key_hex`` interface. During a transition window nodes
can carry both key types and include both signatures in each event.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
    load_pem_private_key,
)

from .models import NodeIdentity


class NodeIdentityManager:
    """Manages cryptographic identity for an Andyria node."""

    def __init__(
        self,
        data_dir: Path,
        node_id: str,
        deployment_class: str = "edge",
    ) -> None:
        self._data_dir = data_dir
        self._node_id = node_id
        self._deployment_class = deployment_class
        self._key_path = data_dir / "identity.pem"
        self._private_key: Optional[Ed25519PrivateKey] = None
        self._identity: Optional[NodeIdentity] = None

    def load_or_create(self) -> NodeIdentity:
        """Load existing identity or generate a new one."""
        import time

        self._data_dir.mkdir(parents=True, exist_ok=True)

        if self._key_path.exists():
            self._private_key = self._load_key()
        else:
            self._private_key = self._generate_key()

        pub = self._private_key.public_key()
        pub_hex = pub.public_bytes(Encoding.Raw, PublicFormat.Raw).hex()

        self._identity = NodeIdentity(
            node_id=self._node_id,
            public_key=pub_hex,
            created_at=int(time.time() * 1e9),
            deployment_class=self._deployment_class,
            capabilities=self._detect_capabilities(),
        )
        return self._identity

    @property
    def private_key(self) -> Ed25519PrivateKey:
        if self._private_key is None:
            raise RuntimeError("Identity not initialized — call load_or_create() first.")
        return self._private_key

    @property
    def identity(self) -> NodeIdentity:
        if self._identity is None:
            raise RuntimeError("Identity not initialized — call load_or_create() first.")
        return self._identity

    def _generate_key(self) -> Ed25519PrivateKey:
        key = Ed25519PrivateKey.generate()
        pem = key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
        self._key_path.touch(mode=0o600)
        self._key_path.write_bytes(pem)
        return key

    def _load_key(self) -> Ed25519PrivateKey:
        pem = self._key_path.read_bytes()
        key = load_pem_private_key(pem, password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError(f"Expected Ed25519 private key, got {type(key)}")
        return key

    def _detect_capabilities(self) -> List[str]:
        caps = ["language", "symbolic"]
        if Path("/dev/hwrng").exists():
            caps.append("hwrng")
        try:
            import llama_cpp  # noqa: F401

            caps.append("llm_local")
        except ImportError:
            pass
        return caps
