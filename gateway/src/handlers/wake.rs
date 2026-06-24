//! Handler for `GET /v1/wake` — pre-warm the scale-to-zero workers service (#293).
//!
//! The sole purpose of this route is to wake the workers Cloud Run service: the
//! act of reaching it triggers the cold start. The handler fetches a Cloud Run
//! ID-token (reusing `workers_id_token_provider`) and performs a single
//! `GET {workers_url}/health`.
//!
//! # Security
//! - Public: no `AuthUser` extractor. Mounted in the public group, subject to the
//!   public-route rate limiting. Anonymous callers must be able to pre-warm (PRD
//!   user story 11).
//! - The per-call timeout (`WAKE_TIMEOUT`) is applied on this request builder only,
//!   NOT on the shared HTTP client, so the global 35 s cap is untouched (PRD AC-9).
//! - Errors never leak upstream detail: warm → `204`, any failure → `503` with the
//!   uniform `{error, request_id}` envelope.

use std::sync::Arc;
use std::time::Duration;

use axum::{
    extract::{Extension, State},
    http::StatusCode,
    response::{IntoResponse, Response},
};

use crate::handlers::workers_proxy::build_error;
use crate::{state::AppState, RequestId};

/// Per-call timeout for the worker `/health` wake request.
///
/// WHY 90 s and not the 30 s external cap from `security.md`: a scale-to-zero cold
/// start (heavy worker boot: SQL pool + IAM token, GCS client, embedder, LLM client,
/// transformers import > 30 s) can exceed 30 s before `/health` answers. The wake
/// route exists precisely to absorb that window. The deviation is bounded to this
/// single route via a per-call `.timeout(...)` override; the shared client stays at
/// 35 s (see #290 / PR for the documented gap).
const WAKE_TIMEOUT: Duration = Duration::from_secs(90);

/// Handler for `GET /v1/wake`.
///
/// Fetches a Cloud Run ID-token, reaches `GET {workers_url}/health` with a per-call
/// 90 s timeout, and returns `204` when the worker is warm or `503` on any failure.
pub async fn wake(
    State(state): State<Arc<AppState>>,
    Extension(req_id): Extension<RequestId>,
) -> Response {
    let request_id = req_id.0;

    let Ok(id_token) = state.workers_id_token_provider.fetch_id_token().await else {
        tracing::warn!(event = "wake.id_token_failed", request_id);
        return build_error(
            &request_id,
            "upstream_unavailable",
            StatusCode::SERVICE_UNAVAILABLE,
        );
    };

    let url = format!("{}/health", state.config.workers_url);
    let result = state
        .http
        .get(&url)
        .timeout(WAKE_TIMEOUT)
        .bearer_auth(secrecy::ExposeSecret::expose_secret(&id_token))
        .send()
        .await;

    match result {
        Ok(response) if response.status().is_success() => StatusCode::NO_CONTENT.into_response(),
        Ok(_) | Err(_) => {
            tracing::warn!(event = "wake.upstream_failed", request_id);
            build_error(
                &request_id,
                "upstream_unavailable",
                StatusCode::SERVICE_UNAVAILABLE,
            )
        }
    }
}
