//! `GET /v1/quality` — public anonymous endpoint returning the latest live Ragas eval run.
//!
//! AC-1: `200`, `application/json; charset=utf-8`, body 6 keys exact when ≥1 live row exists.
//! AC-4: 0 live rows → `200` body `{"status":"no_data"}`.
//! AC-5: no auth extractor — route mounted in `public_api`.
//! AC-7: DB unavailable → 503 `{"error":"upstream_unavailable","request_id":"<uuid>"}`.

use std::{sync::Arc, time::Instant};

use axum::{
    extract::{Extension, State},
    Json,
};
use bigdecimal::BigDecimal;
use chrono::{DateTime, Utc};
use serde::Serialize;
use sqlx::FromRow;

use crate::{errors::ApiError, state::AppState, RequestId};

/// Full row returned by `SELECT … LIMIT 1` when a live run exists (AC-1).
#[derive(Debug, FromRow)]
struct EvalRunRow {
    faithfulness: BigDecimal,
    answer_relevancy: BigDecimal,
    context_precision: BigDecimal,
    context_recall: BigDecimal,
    golden_set_version: String,
    finished_at: DateTime<Utc>,
}

/// Body returned when at least one live row exists (AC-1: 6 keys exact).
#[derive(Debug, Serialize)]
pub struct QualityMetrics {
    /// Ragas faithfulness score, NUMERIC(5,4) serialized as JSON number (AC-3).
    ///
    /// `bigdecimal::serde::json_num` emits a bare JSON number instead of the
    /// default string representation produced by `Serialize` without the
    /// `serde-json` feature (AC-3 / `bigdecimal` 0.4 behaviour).
    #[serde(with = "bigdecimal::serde::json_num")]
    pub faithfulness: BigDecimal,
    /// Ragas `answer_relevancy` score, NUMERIC(5,4) serialized as JSON number (AC-3).
    #[serde(with = "bigdecimal::serde::json_num")]
    pub answer_relevancy: BigDecimal,
    /// Ragas `context_precision` score, NUMERIC(5,4) serialized as JSON number (AC-3).
    #[serde(with = "bigdecimal::serde::json_num")]
    pub context_precision: BigDecimal,
    /// Ragas `context_recall` score, NUMERIC(5,4) serialized as JSON number (AC-3).
    #[serde(with = "bigdecimal::serde::json_num")]
    pub context_recall: BigDecimal,
    /// SHA-256 of the golden set used for this run.
    pub golden_set_version: String,
    /// RFC3339/ISO-8601 UTC timestamp of when the run finished (AC-6).
    pub finished_at: DateTime<Utc>,
}

/// Body returned when no live row exists (AC-4).
#[derive(Debug, Serialize)]
pub struct NoDataResponse {
    /// Literal `"no_data"` — the only key in the body (AC-4).
    pub status: &'static str,
}

/// Response discriminant: either 6-field metrics or `{"status":"no_data"}`.
///
/// `#[serde(untagged)]` ensures the JSON body contains exactly the fields of
/// the inner type — no wrapper key is emitted (AC-1 / AC-4).
#[derive(Debug, Serialize)]
#[serde(untagged)]
pub enum QualityResponse {
    /// Live data available.
    Metrics(QualityMetrics),
    /// No live run stored yet.
    NoData(NoDataResponse),
}

/// Handler: `GET /v1/quality` — public anonymous, returns latest live Ragas metrics.
///
/// No auth extractor — route is mounted directly in `public_api` (AC-5).
/// Uses `sqlx::query_as` (runtime-typed) — no offline cache required.
///
/// # Errors
///
/// Returns `ApiError::UpstreamUnavailable` (→ 503) when:
/// - `db_pool` is `None` (test env without a real DB, or no-pool state).
/// - The `SELECT … LIMIT 1` query fails for any reason.
pub async fn quality(
    Extension(req_id): Extension<RequestId>,
    State(state): State<Arc<AppState>>,
) -> Result<Json<QualityResponse>, ApiError> {
    let request_id = &req_id.0;
    let start = Instant::now();

    let pool = state
        .db_pool
        .as_ref()
        .ok_or(ApiError::UpstreamUnavailable)?;

    let row: Option<EvalRunRow> = sqlx::query_as(
        "SELECT faithfulness, answer_relevancy, context_precision, context_recall, \
         golden_set_version, finished_at \
         FROM eval_runs \
         WHERE runner_mode = 'live' \
         ORDER BY finished_at DESC \
         LIMIT 1",
    )
    .fetch_optional(pool)
    .await
    .map_err(|_| ApiError::UpstreamUnavailable)?;

    let latency_ms = u64::try_from(start.elapsed().as_millis()).unwrap_or(u64::MAX);

    if let Some(r) = row {
        tracing::info!(
            event = "quality.fetched",
            request_id = %request_id,
            latency_ms,
            has_data = true,
        );
        Ok(Json(QualityResponse::Metrics(QualityMetrics {
            faithfulness: r.faithfulness,
            answer_relevancy: r.answer_relevancy,
            context_precision: r.context_precision,
            context_recall: r.context_recall,
            golden_set_version: r.golden_set_version,
            finished_at: r.finished_at,
        })))
    } else {
        tracing::info!(
            event = "quality.fetched",
            request_id = %request_id,
            latency_ms,
            has_data = false,
        );
        Ok(Json(QualityResponse::NoData(NoDataResponse {
            status: "no_data",
        })))
    }
}
