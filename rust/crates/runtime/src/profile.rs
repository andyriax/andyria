//! Deployment profile selection for Andyria nodes.
//!
//! Chooses conservative resource budgets and model size recommendations
//! based on detected hardware, ensuring the system stays within limits
//! on Raspberry Pi and scales up smoothly on more capable hardware.

use serde::{Deserialize, Serialize};

use crate::hardware::HardwareCapabilities;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DeploymentClass {
    /// Raspberry Pi / SBC: ≤ 4 GB RAM, ≤ 4 cores
    Edge,
    /// Laptop / workstation: up to 32 GB RAM, up to 16 cores
    Server,
    /// Multi-node cluster or high-memory server: > 32 GB or > 16 cores
    Cluster,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DeploymentProfile {
    pub class: DeploymentClass,
    /// Maximum parallel tasks
    pub max_tasks: u32,
    /// Recommended model context size in tokens
    pub model_ctx_tokens: u32,
    /// Model size tier: "tiny" (<1 B params), "small" (1-3 B), "medium" (3-13 B)
    pub model_size_tier: String,
    /// RAM budget reserved for the local model (MB)
    pub model_ram_mb: u64,
    /// Target entropy beacon generation interval (ms)
    pub beacon_interval_ms: u64,
}

impl DeploymentProfile {
    pub fn from_capabilities(caps: &HardwareCapabilities) -> Self {
        if caps.is_edge_class || caps.ram_mb <= 1024 {
            Self {
                class: DeploymentClass::Edge,
                max_tasks: 2,
                model_ctx_tokens: 512,
                model_size_tier: "tiny".to_string(),
                model_ram_mb: 512,
                beacon_interval_ms: 5_000,
            }
        } else if caps.ram_mb <= 32_768 && caps.cpu_cores <= 16 {
            Self {
                class: DeploymentClass::Server,
                max_tasks: 8,
                model_ctx_tokens: 2048,
                model_size_tier: "small".to_string(),
                model_ram_mb: 4096,
                beacon_interval_ms: 1_000,
            }
        } else {
            Self {
                class: DeploymentClass::Cluster,
                max_tasks: 32,
                model_ctx_tokens: 8192,
                model_size_tier: "medium".to_string(),
                model_ram_mb: 16_384,
                beacon_interval_ms: 250,
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::hardware::HardwareCapabilities;

    fn fake_caps(ram_mb: u64, cpu_cores: u32) -> HardwareCapabilities {
        HardwareCapabilities {
            ram_mb,
            cpu_cores,
            hwrng: false,
            gpu: false,
            platform: "linux".into(),
            arch: "aarch64".into(),
            is_edge_class: ram_mb <= 4096 && cpu_cores <= 4,
        }
    }

    #[test]
    fn pi_is_edge() {
        let profile = DeploymentProfile::from_capabilities(&fake_caps(4096, 4));
        assert_eq!(profile.class, DeploymentClass::Edge);
        assert_eq!(profile.model_size_tier, "tiny");
    }

    #[test]
    fn laptop_is_server() {
        let profile = DeploymentProfile::from_capabilities(&fake_caps(16_384, 8));
        assert_eq!(profile.class, DeploymentClass::Server);
    }

    #[test]
    fn high_mem_is_cluster() {
        let profile = DeploymentProfile::from_capabilities(&fake_caps(65_536, 32));
        assert_eq!(profile.class, DeploymentClass::Cluster);
    }
}
