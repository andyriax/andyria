"""Checkpoint Attestation Scheme for Andyria distributed consensus.

Enables fast bootstrap from cryptographically-proven snapshots of the ledger.
Uses quorum-signed checkpoints to allow new nodes to join in seconds instead of
hours, while maintaining Byzantine resilience.

Key phases:
  1. Validator Computes Checkpoint: BLAKE3 hash of canonical event list
  2. Validators Vote: Peers verify root_hash and sign if valid
  3. Quorum Assembly: Checkpoint finalized when threshold signatures reached
  4. Bootstrap: New nodes fetch checkpoint + verify quorum signatures
  5. Delta Sync: Fetch events since checkpoint, apply fork-merge to converge
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional, Dict, List, Set
from pathlib import Path

from .models import Event, EventType
from .store import EventStore


@dataclass
class CheckpointSignature:
    """Signature from one validator on a checkpoint."""
    
    validator_node_id: str
    signature: str  # Ed25519 hex
    signed_at_ns: int
    verified: bool = False


@dataclass
class Checkpoint:
    """Cryptographically-signed snapshot of the ledger at a specific height."""
    
    height: int  # Event sequence number (0-indexed)
    root_hash: str  # BLAKE3 of canonical JSON of all events[0:height]
    state_root: str  # BLAKE3 of merged application state at this height
    timestamp_ns: int  # When checkpoint was created
    creator_node_id: str  # Which validator created it
    validator_signatures: Dict[str, CheckpointSignature] = field(default_factory=dict)
    quorum_threshold: int = 3  # Minimum signatures needed for validity
    metadata: Dict = field(default_factory=dict)  # Optional: app-specific data
    
    def to_dict(self) -> dict:
        """Serialize checkpoint (without signatures) for hashing."""
        return {
            "height": self.height,
            "root_hash": self.root_hash,
            "state_root": self.state_root,
            "timestamp_ns": self.timestamp_ns,
            "creator_node_id": self.creator_node_id,
            "quorum_threshold": self.quorum_threshold,
            "metadata": self.metadata,
        }
    
    def to_json(self) -> str:
        """Full serialization including signatures."""
        data = asdict(self)
        # Convert signature objects to dicts
        sigs = {}
        for node_id, sig_obj in self.validator_signatures.items():
            sigs[node_id] = asdict(sig_obj)
        data['validator_signatures'] = sigs
        return json.dumps(data)
    
    @staticmethod
    def from_json(data: str) -> Checkpoint:
        """Deserialize from JSON."""
        obj = json.loads(data)
        # Reconstruct signature objects
        sigs = {}
        for node_id, sig_data in obj.get('validator_signatures', {}).items():
            sigs[node_id] = CheckpointSignature(**sig_data)
        obj['validator_signatures'] = sigs
        return Checkpoint(**obj)
    
    def is_valid(self) -> bool:
        """Check if checkpoint has reached quorum."""
        verified_count = sum(1 for sig in self.validator_signatures.values() if sig.verified)
        return verified_count >= self.quorum_threshold
    
    def signature_count(self) -> int:
        """Get number of valid signatures."""
        return sum(1 for sig in self.validator_signatures.values() if sig.verified)


class CheckpointAttestation:
    """Manages checkpoint creation, voting, and bootstrap verification."""
    
    def __init__(
        self,
        store: EventStore,
        node_id: str,
        quorum_threshold: int = 3,
        checkpoint_dir: Optional[Path] = None,
    ):
        """
        Initialize checkpoint manager.
        
        Args:
            store: EventStore for ledger access
            node_id: This validator's node ID
            quorum_threshold: Min signatures needed for validity
            checkpoint_dir: Directory to persist checkpoints
        """
        self.store = store
        self.node_id = node_id
        self.quorum_threshold = quorum_threshold
        self.checkpoint_dir = checkpoint_dir or Path.home() / ".andyria" / "checkpoints"
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        
        # In-memory checkpoint cache
        self._checkpoints: Dict[int, Checkpoint] = {}
        self._load_persisted_checkpoints()
    
    # ============================================================================
    # Phase 1: Validator Computes Checkpoint
    # ============================================================================
    
    def create_checkpoint(self, height: Optional[int] = None) -> Checkpoint:
        """
        Phase 1: Compute a new checkpoint at the given height.
        
        Creates a BLAKE3 hash of all events up to the given height, representing
        a cryptographic commitment to the ledger state at that point.
        
        Args:
            height: Event count to include (None = use current height)
            
        Returns:
            Checkpoint object (not yet signed; needs validator votes)
        """
        events = self.store.load_all()
        
        if height is None:
            height = len(events)
        elif height > len(events):
            raise ValueError(f"Requested height {height} exceeds event count {len(events)}")
        
        # Phase 1a: Create canonical event list (deterministic ordering)
        events_slice = events[:height]
        
        # Phase 1b: Compute root_hash (BLAKE3 of canonical JSON)
        canonical_form = self._make_canonical_form(events_slice)
        root_hash = hashlib.blake3(canonical_form.encode()).hexdigest()
        
        # Phase 1c: Compute state_root (placeholder; would merge app state)
        state_root = self._compute_state_root(events_slice)
        
        timestamp_ns = int(datetime.now(timezone.utc).timestamp() * 1e9)
        
        checkpoint = Checkpoint(
            height=height,
            root_hash=root_hash,
            state_root=state_root,
            timestamp_ns=timestamp_ns,
            creator_node_id=self.node_id,
            quorum_threshold=self.quorum_threshold,
        )
        
        # Cache locally
        self._checkpoints[height] = checkpoint
        
        return checkpoint
    
    def _make_canonical_form(self, events: List[Event]) -> str:
        """
        Create canonical JSON form of events for deterministic hashing.
        
        Ensures that identical event sets always produce identical hashes,
        regardless of ordering or serialization differences.
        """
        event_dicts = []
        for event in events:
            # Use deterministic JSON serialization
            event_dict = {
                "id": event.id,
                "parent_ids": sorted(event.parent_ids),
                "event_type": str(event.event_type),
                "payload_hash": event.payload_hash,
                "entropy_beacon_id": event.entropy_beacon_id,
                "timestamp_ns": event.timestamp_ns,
                "node_id": event.node_id,
                "signature": event.signature,
            }
            event_dicts.append(event_dict)
        
        # Sort by ID to ensure deterministic order
        event_dicts.sort(key=lambda e: e["id"])
        
        return json.dumps(event_dicts, separators=(",", ":"), sort_keys=True)
    
    def _compute_state_root(self, events: List[Event]) -> str:
        """
        Phase 1c: Compute state_root (application-specific state hash).
        
        In a real system, this would compute a hash of the merged application
        state after applying all events up to this height. For MVP, we compute
        a hash of event metadata.
        """
        state_data = json.dumps(
            [
                {
                    "id": e.id,
                    "timestamp": e.timestamp_ns,
                    "type": str(e.event_type),
                }
                for e in events
            ]
        )
        return hashlib.blake3(state_data.encode()).hexdigest()
    
    # ============================================================================
    # Phase 2-3: Validator Voting & Quorum Assembly
    # ============================================================================
    
    def verify_and_vote(
        self,
        checkpoint: Checkpoint,
        verify_against_ledger: bool = True,
    ) -> Optional[CheckpointSignature]:
        """
        Phase 2-3a: Validator verifies checkpoint and votes (signs).
        
        A validator verifies that:
        1. The root_hash matches locally-computed hash of events[0:height]
        2. No fork detected in the event sequence up to height
        3. state_root is valid
        
        If all checks pass, creates a signed vote.
        
        Args:
            checkpoint: Checkpoint to verify
            verify_against_ledger: If True, verify root_hash matches local ledger
            
        Returns:
            CheckpointSignature if verification passed, None otherwise
        """
        # Phase 2a: Verify root_hash
        if verify_against_ledger:
            events = self.store.load_all()
            if checkpoint.height > len(events):
                print(f"Checkpoint height {checkpoint.height} exceeds local ledger")
                return None
            
            events_slice = events[:checkpoint.height]
            canonical = self._make_canonical_form(events_slice)
            computed_hash = hashlib.blake3(canonical.encode()).hexdigest()
            
            if computed_hash != checkpoint.root_hash:
                print(f"Checkpoint root_hash mismatch: expected {computed_hash}, got {checkpoint.root_hash}")
                return None
        
        # Phase 2b: Verify no fork detected
        from .fork_merge import ForkMergeCoordinator
        coordinator = ForkMergeCoordinator(self.store, self.node_id)
        forks = coordinator.detect_forks()
        if forks:
            print(f"Forks detected; cannot vote on checkpoint")
            return None
        
        # Phase 2c: Verify state_root (delegated to app layer)
        # For MVP, trust that state_root is correct if root_hash matches
        
        # Phase 3a: Create signature
        timestamp_ns = int(datetime.now(timezone.utc).timestamp() * 1e9)
        
        # TODO: In production, sign the checkpoint with Ed25519 private key
        # For MVP, use a placeholder signature
        signature = f"sig_{self.node_id}_{checkpoint.height}_{timestamp_ns}"
        
        vote = CheckpointSignature(
            validator_node_id=self.node_id,
            signature=signature,
            signed_at_ns=timestamp_ns,
            verified=True,
        )
        
        return vote
    
    def assemble_quorum(
        self,
        checkpoint: Checkpoint,
        votes: List[CheckpointSignature],
    ) -> bool:
        """
        Phase 3b: Assemble quorum by collecting validator votes.
        
        Once quorum_threshold signatures are collected, checkpoint is finalized.
        
        Args:
            checkpoint: Checkpoint being voted on
            votes: List of CheckpointSignature objects
            
        Returns:
            True if quorum reached and checkpoint finalized
        """
        # Add votes to checkpoint
        for vote in votes:
            checkpoint.validator_signatures[vote.validator_node_id] = vote
        
        # Check if quorum reached
        if checkpoint.is_valid():
            # Finalize checkpoint
            self._persist_checkpoint(checkpoint)
            
            # Create checkpoint_finalized event
            self._create_finalized_event(checkpoint)
            
            return True
        
        return False
    
    # ============================================================================
    # Phase 4: Bootstrap Node Fetches & Verifies
    # ============================================================================
    
    def fetch_latest_checkpoint(self) -> Optional[Checkpoint]:
        """
        Phase 4a: Fetch latest finalized checkpoint from network.
        
        In a real system, would query multiple peers and verify signatures.
        For MVP, returns highest height checkpoint from local cache.
        
        Returns:
            Latest valid checkpoint, or None if none available
        """
        if not self._checkpoints:
            return None
        
        latest_height = max(self._checkpoints.keys())
        checkpoint = self._checkpoints[latest_height]
        
        if checkpoint.is_valid():
            return checkpoint
        
        return None
    
    def verify_bootstrap_checkpoint(
        self,
        checkpoint: Checkpoint,
        peer_quorum_sigs: Dict[str, CheckpointSignature],
    ) -> bool:
        """
        Phase 4b: New node verifies fetched checkpoint.
        
        Verifies that:
        1. Quorum of trusted validators have signed
        2. All signatures are valid (Ed25519 verification)
        3. root_hash can be locally verified (optional: expensive)
        
        Args:
            checkpoint: Checkpoint from peer
            peer_quorum_sigs: Signatures collected from quorum
            
        Returns:
            True if checkpoint is valid and can be trusted
        """
        # Phase 4b-i: Check signature count
        if len(peer_quorum_sigs) < checkpoint.quorum_threshold:
            print(f"Insufficient signatures: {len(peer_quorum_sigs)} < {checkpoint.quorum_threshold}")
            return False
        
        # Phase 4b-ii: Verify each signature (placeholder)
        # In production: verify Ed25519 signature against validator's public key
        for node_id, sig in peer_quorum_sigs.items():
            if not sig.verified:
                print(f"Signature from {node_id} not verified")
                return False
        
        # Phase 4b-iii: Optionally verify root_hash (expensive for large ledgers)
        # Skip for MVP; trust the quorum signature
        
        return True
    
    def bootstrap_from_checkpoint(self, checkpoint: Checkpoint) -> int:
        """
        Phase 4c: Bootstrap new node from checkpoint.
        
        Loads state from checkpoint without replaying entire ledger.
        In a real system, would restore application state from state_root.
        
        Args:
            checkpoint: Verified checkpoint to bootstrap from
            
        Returns:
            Height at which node is now synchronized
        """
        self._checkpoints[checkpoint.height] = checkpoint
        self._persist_checkpoint(checkpoint)
        
        # TODO: Restore application state from checkpoint.state_root
        
        print(f"Bootstrapped from checkpoint at height {checkpoint.height}")
        return checkpoint.height
    
    # ============================================================================
    # Phase 5: Delta Sync & Convergence
    # ============================================================================
    
    def delta_sync_since_checkpoint(
        self,
        checkpoint: Checkpoint,
    ) -> tuple[int, List[Event]]:
        """
        Phase 5: Fetch events since checkpoint and apply fork-merge.
        
        After bootstrapping from a checkpoint, new node fetches all events
        since that height and applies the fork-merge protocol to converge
        with the network.
        
        Args:
            checkpoint: Checkpoint from which to sync
            
        Returns:
            Tuple of (new_events_count, newly_inserted_events)
        """
        # Fetch events since checkpoint.height
        # In real system: query peer for events with timestamp_ns > checkpoint.timestamp_ns
        
        # For MVP: just apply fork-merge on existing ledger
        from .fork_merge import ForkMergeCoordinator
        coordinator = ForkMergeCoordinator(self.store, self.node_id)
        
        # Detect and annotate any forks
        forks = coordinator.detect_forks()
        for fork_id, fork_info in forks.items():
            coordinator.annotate_fork(fork_id, fork_info)
        
        return (0, [])  # Placeholder
    
    # ============================================================================
    # Persistence & Recovery
    # ============================================================================
    
    def _persist_checkpoint(self, checkpoint: Checkpoint) -> None:
        """Save checkpoint to disk for recovery."""
        path = self.checkpoint_dir / f"checkpoint_{checkpoint.height}.json"
        with open(path, "w") as f:
            f.write(checkpoint.to_json())
    
    def _load_persisted_checkpoints(self) -> None:
        """Load all checkpoints from disk."""
        for path in self.checkpoint_dir.glob("checkpoint_*.json"):
            try:
                with open(path) as f:
                    checkpoint = Checkpoint.from_json(f.read())
                    self._checkpoints[checkpoint.height] = checkpoint
            except Exception as e:
                print(f"Failed to load checkpoint {path}: {e}")
    
    # ============================================================================
    # Event Recording
    # ============================================================================
    
    def _create_finalized_event(self, checkpoint: Checkpoint) -> None:
        """
        Record checkpoint finalization in the ledger.
        
        Creates a CHECKPOINT_FINALIZED event that documents:
        - Which validators signed
        - The checkpoint height & hash
        - Timestamp of finalization
        """
        payload = {
            "checkpoint_height": checkpoint.height,
            "root_hash": checkpoint.root_hash,
            "validator_count": len(checkpoint.validator_signatures),
            "signatures": [
                {
                    "node_id": node_id,
                    "timestamp_ns": sig.signed_at_ns,
                }
                for node_id, sig in checkpoint.validator_signatures.items()
            ],
        }
        
        payload_json = json.dumps(payload, sort_keys=True)
        payload_hash = hashlib.blake3(payload_json.encode()).hexdigest()
        
        event = Event(
            id=f"checkpoint_finalized_{checkpoint.height}",
            parent_ids=[],  # Checkpoint events don't have parents
            event_type=EventType.CHECKPOINT_FINALIZED,
            payload_hash=payload_hash,
            entropy_beacon_id="",
            timestamp_ns=int(datetime.now(timezone.utc).timestamp() * 1e9),
            node_id=self.node_id,
            signature="",  # TODO: sign with private key
        )
        
        self.store.append(event)


__all__ = [
    "CheckpointSignature",
    "Checkpoint",
    "CheckpointAttestation",
]
