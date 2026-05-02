//! Cryptographic primitives for the Andyria ledger.
//!
//! Current scheme: Ed25519 via `ed25519-dalek`.
//!
//! # Post-quantum upgrade path
//! When ML-DSA (CRYSTALS-Dilithium / FIPS 204) is production-stable,
//! replace `NodeKeyPair::sign` / `verify_signature` with a
//! `PostQuantumKeyPair` that exposes the same interface. During a
//! transition window, nodes can carry both key types and include both
//! signatures per event — peers that only know Ed25519 can still verify
//! the classical signature while PQ-capable peers validate ML-DSA.

use ed25519_dalek::{Signature, Signer as DalekSigner, SigningKey, Verifier, VerifyingKey};
use rand_core::OsRng;
use thiserror::Error;

#[derive(Debug, Error)]
pub enum SigningError {
    #[error("signature verification failed")]
    VerificationFailed,
    #[error("invalid key material: {0}")]
    InvalidKey(String),
}

/// Ed25519 keypair for node identity and event signing.
pub struct NodeKeyPair {
    signing_key: SigningKey,
}

impl NodeKeyPair {
    /// Generate a fresh keypair from OS entropy.
    pub fn generate() -> Self {
        let signing_key = SigningKey::generate(&mut OsRng);
        Self { signing_key }
    }

    /// Reconstruct from a raw 32-byte seed (loaded from protected storage).
    pub fn from_bytes(bytes: &[u8; 32]) -> Self {
        let signing_key = SigningKey::from_bytes(bytes);
        Self { signing_key }
    }

    /// Sign `msg`; returns the 64-byte signature hex-encoded.
    pub fn sign(&self, msg: &[u8]) -> String {
        let sig: Signature = self.signing_key.sign(msg);
        hex::encode(sig.to_bytes())
    }

    /// Ed25519 public key as hex string.
    pub fn public_key_hex(&self) -> String {
        hex::encode(self.signing_key.verifying_key().to_bytes())
    }

    /// Raw 32-byte seed for persistence — protect with OS-level ACL (mode 0o600).
    pub fn to_bytes(&self) -> [u8; 32] {
        self.signing_key.to_bytes()
    }
}

/// Verify an Ed25519 signature produced by a `NodeKeyPair`.
pub fn verify_signature(
    public_key_hex: &str,
    message: &[u8],
    signature_hex: &str,
) -> Result<(), SigningError> {
    let pub_bytes =
        hex::decode(public_key_hex).map_err(|e| SigningError::InvalidKey(e.to_string()))?;
    let sig_bytes =
        hex::decode(signature_hex).map_err(|e| SigningError::InvalidKey(e.to_string()))?;

    let pub_key = VerifyingKey::try_from(pub_bytes.as_slice())
        .map_err(|e| SigningError::InvalidKey(e.to_string()))?;
    let sig = Signature::from_slice(&sig_bytes)
        .map_err(|e| SigningError::InvalidKey(e.to_string()))?;

    pub_key
        .verify(message, &sig)
        .map_err(|_| SigningError::VerificationFailed)
}
