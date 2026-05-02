//! Hardware capability detection for Andyria nodes.
//!
//! Runs at startup so the Coordinator can select the right deployment
//! profile, entropy sources, model size tier, and concurrency limits.

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HardwareCapabilities {
    /// Estimated available RAM in megabytes
    pub ram_mb: u64,
    /// Logical CPU cores
    pub cpu_cores: u32,
    /// Hardware RNG device present (/dev/hwrng on Raspberry Pi)
    pub hwrng: bool,
    /// GPU device detectable (heuristic)
    pub gpu: bool,
    /// OS identifier ("linux", "windows", "macos")
    pub platform: String,
    /// CPU architecture ("x86_64", "aarch64", etc.)
    pub arch: String,
    /// True when hardware looks like a Raspberry Pi or other SBC
    pub is_edge_class: bool,
}

/// Detect hardware capabilities of the current node.
pub fn detect_capabilities() -> HardwareCapabilities {
    let ram_mb = detect_ram_mb();
    let cpu_cores = detect_cpu_cores();
    HardwareCapabilities {
        ram_mb,
        cpu_cores,
        hwrng: hwrng_available(),
        gpu: gpu_available(),
        platform: std::env::consts::OS.to_string(),
        arch: std::env::consts::ARCH.to_string(),
        is_edge_class: ram_mb <= 4096 && cpu_cores <= 4,
    }
}

// ── Platform-specific probes ─────────────────────────────────────────────────

fn detect_ram_mb() -> u64 {
    #[cfg(target_os = "linux")]
    {
        if let Ok(content) = std::fs::read_to_string("/proc/meminfo") {
            for line in content.lines() {
                if line.starts_with("MemTotal:") {
                    let parts: Vec<&str> = line.split_whitespace().collect();
                    if parts.len() >= 2 {
                        if let Ok(kb) = parts[1].parse::<u64>() {
                            return kb / 1024;
                        }
                    }
                }
            }
        }
    }
    1024 // Conservative fallback: assume 1 GB
}

fn detect_cpu_cores() -> u32 {
    #[cfg(target_os = "linux")]
    {
        if let Ok(content) = std::fs::read_to_string("/proc/cpuinfo") {
            let count = content.lines().filter(|l| l.starts_with("processor")).count();
            if count > 0 {
                return count as u32;
            }
        }
    }
    1 // Conservative fallback
}

fn hwrng_available() -> bool {
    #[cfg(target_os = "linux")]
    return std::path::Path::new("/dev/hwrng").exists();
    #[cfg(not(target_os = "linux"))]
    return false;
}

fn gpu_available() -> bool {
    #[cfg(target_os = "linux")]
    {
        std::path::Path::new("/dev/nvidia0").exists()
            || std::path::Path::new("/dev/dri/renderD128").exists()
    }
    #[cfg(not(target_os = "linux"))]
    false
}
