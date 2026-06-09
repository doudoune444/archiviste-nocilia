//! `GET /v1/stats` — public anonymous endpoint returning total conversation count.
//!
//! AC-4/AC-5: `{"conversation_count": <exact count(*)>}` — no rounding, no other field.
//! AC-6: no auth extractor — route is mounted in `public_api` (no gate).
//! AC-7: DB unavailable → 503 `{"error":"upstream_unavailable","request_id":"<uuid>"}`.

use std::{sync::Arc, time::Instant};

use axum::{
    extract::{Extension, State},
    response::Response,
};
use serde::Serialize;
use sqlx::FromRow;

use crate::{errors::ApiError, handlers::json_utf8, state::AppState, RequestId};

/// Response body for `GET /v1/stats` (AC-4: exactly one field).
#[derive(Debug, Serialize)]
pub struct StatsResponse {
    /// Exact `count(*)` of all rows in `conversations` (AC-4/AC-5, decision D-1).
    pub conversation_count: i64,
}

/// Count row for the `SELECT count(*) FROM conversations` query.
#[derive(Debug, FromRow)]
struct CountRow {
    count: i64,
}

/// Handler: `GET /v1/stats` — public anonymous, returns exact conversation count.
///
/// No auth extractor — route is mounted directly in `public_api` (AC-6).
/// Uses `sqlx::query_as` (runtime-typed) — no offline cache required (tickets.rs precedent).
///
/// # Errors
///
/// Returns `ApiError::UpstreamUnavailable` (→ 503) when:
/// - `db_pool` is `None` (test env without a real DB, or no-pool state).
/// - The `SELECT count(*)` query fails for any reason.
pub async fn stats(
    Extension(req_id): Extension<RequestId>,
    State(state): State<Arc<AppState>>,
) -> Result<Response, ApiError> {
    let request_id = &req_id.0;
    let start = Instant::now();

    let pool = state
        .db_pool
        .as_ref()
        .ok_or(ApiError::UpstreamUnavailable)?;

    let row: CountRow = sqlx::query_as("SELECT count(*) AS count FROM conversations")
        .fetch_one(pool)
        .await
        .map_err(|_| ApiError::UpstreamUnavailable)?;

    let latency_ms = u64::try_from(start.elapsed().as_millis()).unwrap_or(u64::MAX);
    let conversation_count = row.count;

    tracing::info!(
        event = "stats.usage",
        request_id = %request_id,
        latency_ms,
        conversation_count,
    );

    Ok(json_utf8(StatsResponse { conversation_count }))
}
