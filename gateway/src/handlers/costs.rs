//! `GET /v1/costs` — public anonymous endpoint returning a monthly GCP cost estimate.
//!
//! The estimate is a pure `volume × tariff` model computed gateway-side; no GCP
//! billing API is called (#275). Volumes are read live from the DB on each call
//! over a 30-day rolling window; tariffs (public GCP unit prices in EUR) come
//! from configuration loaded at boot with no hardcoded default fallback
//! (`CostTariffs`, security.md).
//!
//! Response contract (self-describing via `estimated` + `period`):
//! ```json
//! { "currency": "EUR", "period": "rolling_30d", "estimated": true,
//!   "total_eur": 12.34,
//!   "services": { "postgres": 8.00, "gcs": 0.50, "workers": 3.84 },
//!   "computed_at": "<RFC3339 UTC>" }
//! ```

use std::{sync::Arc, time::Instant};

use axum::{
    extract::{Extension, State},
    response::Response,
};
use serde::Serialize;
use sqlx::FromRow;

use crate::{
    config::CostTariffs, errors::ApiError, handlers::json_utf8, state::AppState, RequestId,
};

/// Number of bytes in one gigabyte (GCP storage prices are per 10^9 bytes).
const BYTES_PER_GB: f64 = 1_000_000_000.0;

/// Live usage volumes read from the DB over a 30-day rolling window.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Volumes {
    /// Total `PostgreSQL` database size in bytes (`pg_database_size`).
    pub database_size_bytes: i64,
    /// Total conversation bytes persisted (proxy for GCS object bytes).
    pub conversation_bytes_stored: i64,
    /// Number of logged requests in the last 30 days (`request_count_30d`).
    pub request_count_30d: i64,
}

/// Per-service amounts plus the total, all in EUR (rounded to cents).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Costs {
    /// Estimated monthly Postgres cost.
    pub postgres_eur: f64,
    /// Estimated monthly GCS cost.
    pub gcs_eur: f64,
    /// Estimated monthly workers cost.
    pub workers_eur: f64,
    /// Sum of the three services.
    pub total_eur: f64,
}

/// Round a EUR amount to two decimal places (cents).
fn round_to_cents(amount: f64) -> f64 {
    (amount * 100.0).round() / 100.0
}

/// Pure cost estimation: `volumes × tariffs → per-service amounts + total`.
///
/// - `postgres = instance_fixe + db_GB × storage_per_GB`
/// - `gcs = stored_GB × storage_per_GB`
/// - `workers = request_count_30d × per_request`
/// - `total = postgres + gcs + workers`
///
/// All amounts are rounded to cents; the total is the sum of the rounded
/// services so the displayed lines always add up to the displayed total.
// Volumes (byte counts, request counts) are far below 2^52 in any realistic
// deployment; the f64 conversion for the GB/cost arithmetic loses no meaningful
// precision and the result is rounded to cents anyway.
#[allow(clippy::cast_precision_loss)]
#[must_use]
pub fn estimate_costs(tariffs: &CostTariffs, volumes: &Volumes) -> Costs {
    let database_size_gb = volumes.database_size_bytes as f64 / BYTES_PER_GB;
    let conversation_gb = volumes.conversation_bytes_stored as f64 / BYTES_PER_GB;

    let postgres_eur = round_to_cents(
        tariffs.postgres_instance_eur + database_size_gb * tariffs.postgres_storage_per_gb_eur,
    );
    let gcs_eur = round_to_cents(conversation_gb * tariffs.gcs_storage_per_gb_eur);
    let workers_eur =
        round_to_cents(volumes.request_count_30d as f64 * tariffs.workers_per_request_eur);
    let total_eur = round_to_cents(postgres_eur + gcs_eur + workers_eur);

    Costs {
        postgres_eur,
        gcs_eur,
        workers_eur,
        total_eur,
    }
}

/// Currency of every amount in the response (ISO 4217).
const CURRENCY: &str = "EUR";
/// Usage window the variable terms are computed over.
const PERIOD: &str = "rolling_30d";

/// Per-service breakdown nested under `services` in the response body.
#[derive(Debug, Serialize)]
struct ServiceCosts {
    postgres: f64,
    gcs: f64,
    workers: f64,
}

/// Response body for `GET /v1/costs` (exact contract — #275).
#[derive(Debug, Serialize)]
struct CostsResponse {
    currency: &'static str,
    period: &'static str,
    estimated: bool,
    total_eur: f64,
    services: ServiceCosts,
    computed_at: String,
}

/// Row for the single combined volumes query.
#[derive(Debug, FromRow)]
struct VolumesRow {
    database_size_bytes: i64,
    conversation_bytes_stored: i64,
    request_count_30d: i64,
}

/// Handler: `GET /v1/costs` — public anonymous, returns the cost estimate.
///
/// No auth extractor — route is mounted directly in `public_api`.
/// Per-request computation (indexed counts, cheap); no snapshot cache.
///
/// # Errors
///
/// Returns `ApiError::UpstreamUnavailable` (→ 503) when the DB pool is absent
/// or the volumes query fails. Tariffs are validated at boot (`CostTariffs`).
pub async fn costs(
    Extension(req_id): Extension<RequestId>,
    State(state): State<Arc<AppState>>,
) -> Result<Response, ApiError> {
    let request_id = &req_id.0;
    let start = Instant::now();

    let pool = state
        .db_pool
        .as_ref()
        .ok_or(ApiError::UpstreamUnavailable)?;

    let row: VolumesRow = sqlx::query_as(
        "SELECT \
            pg_database_size(current_database())::bigint AS database_size_bytes, \
            COALESCE((SELECT SUM(octet_length(content)) FROM conversation_messages), 0)::bigint \
                AS conversation_bytes_stored, \
            (SELECT count(*) FROM query_log WHERE created_at >= NOW() - INTERVAL '30 days')::bigint \
                AS request_count_30d",
    )
    .fetch_one(pool)
    .await
    .map_err(|_| ApiError::UpstreamUnavailable)?;

    let volumes = Volumes {
        database_size_bytes: row.database_size_bytes,
        conversation_bytes_stored: row.conversation_bytes_stored,
        request_count_30d: row.request_count_30d,
    };
    let estimate = estimate_costs(&state.config.cost_tariffs, &volumes);

    let latency_ms = u64::try_from(start.elapsed().as_millis()).unwrap_or(u64::MAX);
    tracing::info!(
        event = "costs.usage",
        request_id = %request_id,
        latency_ms,
        total_eur = estimate.total_eur,
        request_count_30d = volumes.request_count_30d,
    );

    let body = CostsResponse {
        currency: CURRENCY,
        period: PERIOD,
        estimated: true,
        total_eur: estimate.total_eur,
        services: ServiceCosts {
            postgres: estimate.postgres_eur,
            gcs: estimate.gcs_eur,
            workers: estimate.workers_eur,
        },
        computed_at: chrono::Utc::now().to_rfc3339(),
    };

    Ok(json_utf8(body))
}
