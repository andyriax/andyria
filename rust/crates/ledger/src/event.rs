//! Signed event types for the Andyria append-only event log.

use serde::{Deserialize, Serialize};
use thiserror::Error;

use crate::crypto::NodeKeyPair;

#[derive(Debug, Error)]
pub enum EventError {
    #[error("serialization error: {0}")]
    Serialization(String),
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EventType {
    Request,
    Plan,
    TaskResult,
    Response,
    EntropyBeacon,
    NodeIdentity,
    Checkpoint,
}

/// A signed, immutable entry in the Andyria event log.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Event {
    /// BLAKE3 hash of (sorted parent_ids ‖ payload_hash ‖ entropy_beacon_id ‖ timestamp_ns ‖ node_id)
    pub id: String,
    pub parent_ids: Vec<String>,
    pub event_type: EventType,
    /// BLAKE3 hash of canonical payload bytes
    pub payload_hash: String,
    /// References a signed EntropyBeacon — raw entropy NOT included in this hash
    pub entropy_beacon_id: String,
    pub timestamp_ns: u128,
    pub node_id: String,
    /// Ed25519 signature over canonical JSON
    pub signature: String,
}

impl Event {
    /// Canonical JSON bytes used for signing and verification.
    pub fn canonical_bytes_for_signing(&self) -> Vec<u8> {
        serde_json::to_vec(&serde_json::json!({
            "id":                self.id,
            "parent_ids":        self.parent_ids,
            "event_type":        self.event_type,
            "payload_hash":      self.payload_hash,
            "entropy_beacon_id": self.entropy_beacon_id,
            "timestamp_ns":      self.timestamp_ns,
            "node_id":           self.node_id,
        }))
        .expect("canonical serialization must not fail")
    }
}

/// Builder for creating signed `Event` records.
pub struct EventBuilder {
    parent_ids: Vec<String>,
    event_type: EventType,
    payload: Vec<u8>,
    entropy_beacon_id: String,
    timestamp_ns: u128,
    node_id: String,
}

impl EventBuilder {
    pub fn new(
        event_type: EventType,
        payload: Vec<u8>,
        entropy_beacon_id: impl Into<String>,
        timestamp_ns: u128,
        node_id: impl Into<String>,
    ) -> Self {
        Self {
            parent_ids: Vec::new(),
            event_type,
            payload,
            entropy_beacon_id: entropy_beacon_id.into(),
            timestamp_ns,
            node_id: node_id.into(),
        }
    }

    pub fn with_parents(mut self, parents: Vec<String>) -> Self {
        self.parent_ids = parents;
        self
    }

    /// Build and sign the event.
    pub fn build(self, keypair: &NodeKeyPair) -> Event {
        let payload_hash = blake3::hash(&self.payload).to_hex().to_string();

        let mut sorted_parents = self.parent_ids.clone();
        sorted_parents.sort();

        let id_input = serde_json::to_vec(&serde_json::json!({
            "parent_ids":        sorted_parents,
            "payload_hash":      payload_hash,
            "entropy_beacon_id": self.entropy_beacon_id,
            "timestamp_ns":      self.timestamp_ns,
            "node_id":           self.node_id,
        }))
        .expect("id input serialization must not fail");
        let event_id = blake3::hash(&id_input).to_hex().to_string();

        let mut event = Event {
            id: event_id,
            parent_ids: self.parent_ids,
            event_type: self.event_type,
            payload_hash,
            entropy_beacon_id: self.entropy_beacon_id,
            timestamp_ns: self.timestamp_ns,
            node_id: self.node_id,
            signature: String::new(),
        };

        let canonical = event.canonical_bytes_for_signing();
        event.signature = keypair.sign(&canonical);
        event
    }
}
