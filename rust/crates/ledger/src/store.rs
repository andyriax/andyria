//! Append-only NDJSON event store for the Andyria ledger.
//!
//! Each line of `events.ndjson` is one JSON-serialized `Event`.
//! A flat index directory maps event IDs to a sentinel file for O(1)
//! existence checks without loading the full log.

use std::fs;
use std::io::{BufWriter, Write};
use std::path::{Path, PathBuf};

use thiserror::Error;

use crate::event::Event;

#[derive(Debug, Error)]
pub enum StoreError {
    #[error("io error: {0}")]
    Io(#[from] std::io::Error),
    #[error("serialization error: {0}")]
    Serialization(#[from] serde_json::Error),
    #[error("event not found: {0}")]
    NotFound(String),
}

/// Append-only event log stored as NDJSON.
///
/// Each node maintains its own local log. Peer replication is done by
/// comparing event ID sets and syncing missing events — the content-hash
/// ID ensures idempotent appends across peers.
pub struct EventStore {
    log_path: PathBuf,
    index_dir: PathBuf,
}

impl EventStore {
    pub fn open(data_dir: &Path) -> Result<Self, StoreError> {
        let log_dir = data_dir.join("ledger");
        fs::create_dir_all(&log_dir)?;
        let index_dir = log_dir.join("index");
        fs::create_dir_all(&index_dir)?;

        Ok(Self {
            log_path: log_dir.join("events.ndjson"),
            index_dir,
        })
    }

    /// Append an event to the log. Returns `false` (no-op) for duplicate IDs.
    pub fn append(&self, event: &Event) -> Result<bool, StoreError> {
        if self.contains(&event.id)? {
            return Ok(false);
        }

        let line = serde_json::to_string(event)?;
        let file = fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&self.log_path)?;
        let mut writer = BufWriter::new(file);
        writeln!(writer, "{}", line)?;
        writer.flush()?;

        // Write index sentinel
        fs::write(self.index_dir.join(&event.id), event.id.as_bytes())?;
        Ok(true)
    }

    /// Returns `true` if an event with this ID has been appended.
    pub fn contains(&self, event_id: &str) -> Result<bool, StoreError> {
        Ok(self.index_dir.join(event_id).exists())
    }

    /// Load all events in append order.
    pub fn load_all(&self) -> Result<Vec<Event>, StoreError> {
        if !self.log_path.exists() {
            return Ok(Vec::new());
        }
        let content = fs::read_to_string(&self.log_path)?;
        let mut events = Vec::new();
        for line in content.lines() {
            let line = line.trim();
            if !line.is_empty() {
                events.push(serde_json::from_str::<Event>(line)?);
            }
        }
        Ok(events)
    }

    /// Count of events currently in the log.
    pub fn count(&self) -> Result<usize, StoreError> {
        if !self.log_path.exists() {
            return Ok(0);
        }
        let content = fs::read_to_string(&self.log_path)?;
        Ok(content.lines().filter(|l| !l.trim().is_empty()).count())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        crypto::NodeKeyPair,
        event::{EventBuilder, EventType},
    };
    use std::time::{SystemTime, UNIX_EPOCH};

    #[test]
    fn append_and_reload() {
        let dir = tempfile::tempdir().unwrap();
        let store = EventStore::open(dir.path()).unwrap();
        let keypair = NodeKeyPair::generate();
        let ts = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();

        let event = EventBuilder::new(
            EventType::Checkpoint,
            b"test payload".to_vec(),
            "beacon-001",
            ts,
            "test-node",
        )
        .build(&keypair);

        assert!(store.append(&event).unwrap());
        // Idempotent re-append
        assert!(!store.append(&event).unwrap());

        let loaded = store.load_all().unwrap();
        assert_eq!(loaded.len(), 1);
        assert_eq!(loaded[0].id, event.id);
    }
}
