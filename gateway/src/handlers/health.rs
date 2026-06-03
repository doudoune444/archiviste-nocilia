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
    Ok(Json(HealthResponse {
        status: probe_workers(&state).await,
        version: state.config.version.clone(),
    }))
}

/// Probe the workers `/health` endpoint and return the aggregate status string.
///
/// The workers Cloud Run service requires an IAM-signed ID token (internal
/// ingress + `run.invoker`), so the probe must authenticate exactly like the
/// chat path (`forward_to_workers`): fetch a Google-signed ID token and attach
/// it as `Authorization: Bearer`. An unauthenticated probe gets 401/403 and
/// always degrades.
///
/// Stays infallible: any failure — token fetch or workers call — maps to
/// `"degraded"`, never an error. The caller always returns HTTP 200.
async fn probe_workers(state: &AppState) -> &'static str {
    let Ok(id_token) = state.workers_id_token_provider.fetch_id_token().await else {
        tracing::warn!(event = "health.id_token_failed");
        return "degraded";
    };

    let workers_health = state
        .http
        .get(format!("{}/health", state.config.workers_url))
        .bearer_auth(secrecy::ExposeSecret::expose_secret(&id_token))
        .send()
        .await;

    match workers_health {
        Ok(r) if r.status().is_success() => "ok",
        _ => "degraded",
    }
}
