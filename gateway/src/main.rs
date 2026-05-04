//! Gateway binary entry point.

use anyhow::Result;
use archiviste_gateway::{config::Config, run};

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(tracing_subscriber::EnvFilter::from_default_env())
        .json()
        .init();

    let config = Config::from_env()?;
    run(config).await
}
