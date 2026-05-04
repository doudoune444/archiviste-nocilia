//! Archiviste gateway — public Axum HTTP layer.
//!
//! Exposes the public REST API and proxies internal calls to the workers tier.

pub mod config;
pub mod handlers;
pub mod state;

use anyhow::Result;
use axum::{routing::get, Router};
use std::sync::Arc;
use tower_http::trace::TraceLayer;

use crate::{config::Config, state::AppState};

/// Bind to `config.bind_addr` and serve the gateway router until shutdown.
///
/// # Errors
///
/// Returns an error if `AppState` initialization fails, the TCP listener
/// cannot bind, or the Axum server exits with an error.
pub async fn run(config: Config) -> Result<()> {
    let addr = config.bind_addr.clone();
    let state = Arc::new(AppState::new(config)?);
    let app = router(state);

    let listener = tokio::net::TcpListener::bind(&addr).await?;
    tracing::info!(%addr, "gateway listening");
    axum::serve(listener, app).await?;
    Ok(())
}

/// Build the gateway router with all routes wired and shared state attached.
pub fn router(state: Arc<AppState>) -> Router {
    Router::new()
        .route("/healthz", get(handlers::health::healthz))
        .with_state(state)
        .layer(TraceLayer::new_for_http())
}
