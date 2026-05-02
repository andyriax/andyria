pub mod hardware;
pub mod profile;

pub use hardware::{detect_capabilities, HardwareCapabilities};
pub use profile::{DeploymentClass, DeploymentProfile};
