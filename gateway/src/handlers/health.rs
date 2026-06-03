//! Health-check handler. Aggregates gateway + workers liveness.

use axum::{extract::State, http::StatusCode, Json};
use serde::Serialize;
use std::sync::Arc;

use crate::state::AppState;

/// JSON payload returned by `GET /healthz`.
#[derive(Serialize)]
pub struct HealthResponse {
    /// `"ok"` when the workers tier responded successfully, `"degraded"` otherwise.
    pub status: &'static str,
    /// Gateway crate version.
    pub version: String,
}

/// Liveness endpoint. Probes the workers `/health` and reports an aggregate.
///
/// Served at both `/healthz` and `/health`; the latter exists because Cloud Run's
/// public frontend reserves the literal `/healthz` path and 404s it before the
/// container sees it. The internal workers probe targets `/health` for the same reason.
///
/// Returns HTTP 200 in both healthy and degraded states. The body's `status`
/// field carries the aggregate; consumers must read it to differentiate.
///
/// # Errors
///
/// Returns `StatusCode::INTERNAL_SERVER_ERROR` only on unexpected handler
/// failure (currently never produced).
pub async fn healthz(
    State(state): State<Arc<AppState>>,
) -> Result<Json<HealthResponse>, StatusCode> {
    let workers_health = state
        .http
        .get(format!("{}/health", state.config.workers_url))
        .send()
        .await;

    let status = match workers_health {
        Ok(r) if r.status().is_success() => "ok",
        _ => "degraded",
    };

    Ok(Json(HealthResponse {
        status,
        version: state.config.version.clone(),
    }))
}
