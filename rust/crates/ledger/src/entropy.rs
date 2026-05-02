//! Physical entropy collection and signed beacon generation for Andyria.
//!
//! # Design invariant
//! Event content hashes include `beacon.id` — NOT the raw entropy bytes.
//! This preserves hash determinism (any peer can re-derive the same event
//! ID given the same inputs) while anchoring every event to physical-world
//! randomness at the originating node. The beacon itself is stored
//! separately and is independently verifiable via its Ed25519 signature.

use std::time::{SystemTime, UNIX_EPOCH};

use rand_core::{OsRng, RngCore};
use serde::{Deserialize, Serialize};
use thiserror::Error;

use crate::crypto::NodeKeyPair;

#[derive(Debug, Error)]
pub enum EntropyError {
    #[error("all entropy sources failed")]
    AllSourcesFailed,
    #[error("io error: {0}")]
    Io(String),
}

/// Identifies a physical entropy source available to this node.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PhysicalEntropySource {
    OsRng,
    ClockJitter,
    HwRng,
    Thermal,
}

impl PhysicalEntropySource {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::OsRng => "os_rng",
            Self::ClockJitter => "clock_jitter",
            Self::HwRng => "hw_rng",
            Self::Thermal => "thermal",
        }
    }

    pub fn available(&self) -> bool {
        match self {
            Self::OsRng | Self::ClockJitter => true,
            Self::HwRng => {
                #[cfg(target_os = "linux")]
                return std::path::Path::new("/dev/hwrng").exists();
                #[cfg(not(target_os = "linux"))]
                return false;
            }
            Self::Thermal => {
                #[cfg(target_os = "linux")]
                return std::path::Path::new("/sys/class/thermal").exists();
                #[cfg(not(target_os = "linux"))]
                return false;
            }
        }
    }

    /// Collect raw entropy bytes from this source into `buf`.
    pub fn collect(&self, buf: &mut Vec<u8>) -> Result<(), EntropyError> {
        match self {
            Self::OsRng => {
                let mut raw = [0u8; 64];
                OsRng.fill_bytes(&mut raw);
                buf.extend_from_slice(&raw);
                Ok(())
            }
            Self::ClockJitter => {
                collect_clock_jitter(buf);
                Ok(())
            }
            Self::HwRng => collect_hwrng(buf),
            Self::Thermal => {
                collect_thermal(buf);
                Ok(())
            }
        }
    }
}

/// A signed, auditable entropy beacon.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EntropyBeacon {
    /// BLAKE3 hash of (raw_entropy_hash ‖ nonce ‖ timestamp_ns ‖ node_id)
    pub id: String,
    pub timestamp_ns: u128,
    pub sources: Vec<String>,
    /// BLAKE3 hash of all collected + mixed + timestamp-bound raw bytes
    pub raw_entropy_hash: String,
    /// 32 bytes whitened entropy, hex; usable as nonce / salt
    pub nonce: String,
    pub node_id: String,
    /// Ed25519 signature over canonical JSON (see `canonical_bytes`)
    pub signature: String,
}

impl EntropyBeacon {
    /// Deterministic canonical bytes used for signing and verification.
    pub fn canonical_bytes(&self) -> Vec<u8> {
        serde_json::to_vec(&serde_json::json!({
            "id":               self.id,
            "timestamp_ns":     self.timestamp_ns,
            "sources":          self.sources,
            "raw_entropy_hash": self.raw_entropy_hash,
            "nonce":            self.nonce,
            "node_id":          self.node_id,
        }))
        .expect("canonical serialization must not fail")
    }
}

/// Collects physical entropy from multiple sources and produces signed beacons.
pub struct EntropyCollector {
    node_id: String,
    sources: Vec<PhysicalEntropySource>,
}

impl EntropyCollector {
    pub fn new(
        node_id: impl Into<String>,
        sources: Option<Vec<PhysicalEntropySource>>,
    ) -> Self {
        let default_sources = vec![
            PhysicalEntropySource::OsRng,
            PhysicalEntropySource::ClockJitter,
            PhysicalEntropySource::HwRng,
            PhysicalEntropySource::Thermal,
        ];

        let mut active = sources
            .unwrap_or(default_sources)
            .into_iter()
            .filter(|s| s.available())
            .collect::<Vec<_>>();

        // OsRng is always required as the minimum fallback
        if !active.contains(&PhysicalEntropySource::OsRng) {
            active.insert(0, PhysicalEntropySource::OsRng);
        }

        Self {
            node_id: node_id.into(),
            sources: active,
        }
    }

    /// Collect physical entropy and produce a signed `EntropyBeacon`.
    pub fn generate(&self, keypair: &NodeKeyPair) -> Result<EntropyBeacon, EntropyError> {
        let timestamp_ns = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos();

        // 1. Collect from all available sources; XOR-mix into one buffer
        let mut combined: Vec<u8> = Vec::new();
        let mut active_sources: Vec<String> = Vec::new();

        for source in &self.sources {
            let mut buf = Vec::new();
            if source.collect(&mut buf).is_ok() && !buf.is_empty() {
                xor_mix(&mut combined, &buf);
                active_sources.push(source.as_str().to_string());
            }
        }

        if combined.is_empty() {
            return Err(EntropyError::AllSourcesFailed);
        }

        // 2. Bind to timestamp + node_id to prevent cross-node replay
        combined.extend_from_slice(&timestamp_ns.to_be_bytes());
        combined.extend_from_slice(self.node_id.as_bytes());

        // 3. Whiten
        let raw_entropy_hash = blake3::hash(&combined).to_hex().to_string();

        // 4. Derive nonce (domain-separated)
        let nonce_key = blake3::derive_key("andyria nonce 2026-04", &combined);
        let nonce_bytes = blake3::keyed_hash(&nonce_key, &combined);
        let nonce = hex::encode(nonce_bytes.as_bytes());

        // 5. Derive beacon ID
        let id_input = serde_json::to_vec(&serde_json::json!({
            "raw_entropy_hash": raw_entropy_hash,
            "nonce":            nonce,
            "timestamp_ns":     timestamp_ns,
            "node_id":          self.node_id,
        }))
        .expect("id input serialization must not fail");
        let beacon_id = blake3::hash(&id_input).to_hex().to_string();

        // 6. Sign
        let mut beacon = EntropyBeacon {
            id: beacon_id,
            timestamp_ns,
            sources: active_sources,
            raw_entropy_hash,
            nonce,
            node_id: self.node_id.clone(),
            signature: String::new(),
        };
        let canonical = beacon.canonical_bytes();
        beacon.signature = keypair.sign(&canonical);

        Ok(beacon)
    }
}

// ── Helpers ─────────────────────────────────────────────────────────────────

/// XOR-mix `src` into `dst`, extending `dst` if needed.
fn xor_mix(dst: &mut Vec<u8>, src: &[u8]) {
    if dst.is_empty() {
        dst.extend_from_slice(src);
        return;
    }
    let min = dst.len().min(src.len());
    for i in 0..min {
        dst[i] ^= src[i];
    }
    if src.len() > dst.len() {
        dst.extend_from_slice(&src[dst.len()..]);
    }
}

fn collect_clock_jitter(buf: &mut Vec<u8>) {
    use std::time::Instant;
    let mut prev = Instant::now();
    for _ in 0..128 {
        let now = Instant::now();
        buf.extend_from_slice(&now.duration_since(prev).subsec_nanos().to_be_bytes());
        prev = now;
    }
}

fn collect_hwrng(buf: &mut Vec<u8>) -> Result<(), EntropyError> {
    #[cfg(target_os = "linux")]
    {
        use std::io::Read;
        let mut f = std::fs::File::open("/dev/hwrng")
            .map_err(|e| EntropyError::Io(e.to_string()))?;
        let mut tmp = [0u8; 64];
        f.read_exact(&mut tmp).map_err(|e| EntropyError::Io(e.to_string()))?;
        buf.extend_from_slice(&tmp);
        Ok(())
    }
    #[cfg(not(target_os = "linux"))]
    Err(EntropyError::Io("hwrng not available on this platform".into()))
}

fn collect_thermal(buf: &mut Vec<u8>) {
    #[cfg(target_os = "linux")]
    {
        let base = std::path::Path::new("/sys/class/thermal");
        if !base.exists() {
            return;
        }
        if let Ok(entries) = std::fs::read_dir(base) {
            for entry in entries.flatten() {
                let temp_path = entry.path().join("temp");
                if let Ok(s) = std::fs::read_to_string(&temp_path) {
                    if let Ok(val) = s.trim().parse::<u32>() {
                        buf.extend_from_slice(&val.to_be_bytes());
                    }
                }
            }
        }
    }
}
