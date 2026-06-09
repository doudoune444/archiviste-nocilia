//! `GET /v1/status` — public anonymous endpoint returning aggregate service health.
//!
//! AC-1: body `{status, dependencies: {postgres, gcs, workers}, checked_at}`.
//! AC-2: each dep = `{status: "ok"|"down", latency_ms}` — enum closed, no detail leak.
//! AC-3: root `ok` ⟺ all 3 deps `ok`.
//! AC-5/AC-6: `200` always — no `5xx` path, no `503`.
//! AC-7: no auth extractor — mounted in `public_api`.
//! AC-8: `PROBE_TIMEOUT` 2 s per probe via `tokio::time::timeout`.
//! AC-9: 3 probes run via `tokio::join!` (parallel).
//! AC-10: result cached via `HealthSnapshotCache` (10 s TTL, in `AppState`).
//! AC-11: probe error → `down` + `tracing::warn`; no detail in body.

use std::sync::Arc;
use std::time::{Duration, Instant};

use axum::{extract::State, response::Response};
use serde::Serialize;

use crate::{handlers::json_utf8, state::AppState};

/// Hard per-probe timeout (AC-8 / D-4).
const PROBE_TIMEOUT: Duration = Duration::from_secs(2);

/// Per-dependency health entry (AC-2).
#[derive(Debug, Clone, Serialize)]
pub struct DepStatus {
    /// `"ok"` or `"down"` — closed enum (AC-2).
    pub status: &'static str,
    /// Wall-clock time of the probe in milliseconds (AC-2).
    pub latency_ms: u64,
}

/// Aggregate dependencies object (AC-1).
#[derive(Debug, Clone, Serialize)]
pub struct Dependencies {
    /// `PostgreSQL` reachability (AC-1).
    pub postgres: DepStatus,
    /// GCS bucket reachability (AC-1).
    pub gcs: DepStatus,
    /// Workers service reachability (AC-1, AC-12).
    pub workers: DepStatus,
}

/// Full response body for `GET /v1/status` (AC-1).
#[derive(Debug, Clone, Serialize)]
pub struct StatusResponse {
    /// `"ok"` or `"degraded"` (AC-3).
    pub status: &'static str,
    /// Per-dependency statuses (AC-1).
    pub dependencies: Dependencies,
    /// RFC3339 UTC timestamp of snapshot production (AC-4).
    pub checked_at: String,
}

/// `GET /v1/status` handler — anonymous, 200-always (AC-5/AC-6/AC-7).
///
/// Delegates to the `HealthSnapshotCache` on `AppState`; the cache runs the
/// three probes in parallel when a fresh snapshot is needed (AC-9/AC-10).
pub async fn status(State(state): State<Arc<AppState>>) -> Response {
    let snapshot = state
        .health_cache
        .get_or_refresh(|| run_probes(Arc::clone(&state)))
        .await;
    json_utf8(snapshot)
}

/// Run all 3 probes in parallel (AC-9) and build a `StatusResponse`.
///
/// Each probe is wrapped with `tokio::time::timeout(PROBE_TIMEOUT, …)` (AC-8).
async fn run_probes(state: Arc<AppState>) -> StatusResponse {
    let (postgres, gcs, workers) = tokio::join!(
        probe_with_timeout("postgres", probe_postgres(Arc::clone(&state))),
        probe_with_timeout("gcs", probe_gcs(Arc::clone(&state))),
        probe_with_timeout("workers", probe_workers(Arc::clone(&state))),
    );

    let root_status = aggregate_status(&postgres, &gcs, &workers);

    StatusResponse {
        status: root_status,
        dependencies: Dependencies {
            postgres,
            gcs,
            workers,
        },
        // Placeholder: HealthSnapshotCache::get_or_refresh always overwrites checked_at
        // with the pre-probe timestamp before serving the snapshot (AC-4).
        checked_at: String::new(),
    }
}

/// Wrap a probe future with `PROBE_TIMEOUT`; timeout → `down` (AC-8).
async fn probe_with_timeout(
    dep: &'static str,
    fut: impl std::future::Future<Output = DepStatus>,
) -> DepStatus {
    match tokio::time::timeout(PROBE_TIMEOUT, fut).await {
        Ok(result) => result,
        Err(_elapsed) => {
            // AC-8: timeout → down; log without host/message (AC-11).
            tracing::warn!(event = "status.probe.down", dep, reason = "timeout");
            DepStatus {
                status: "down",
                latency_ms: u64::try_from(PROBE_TIMEOUT.as_millis()).unwrap_or(u64::MAX),
            }
        }
    }
}

/// Probe `PostgreSQL`: `SELECT 1` via pool if available, else `down` (AC-1 / plan).
async fn probe_postgres(state: Arc<AppState>) -> DepStatus {
    let Some(pool) = state.db_pool.as_ref() else {
        // No pool configured → down (not an error, AC-6).
        tracing::warn!(
            event = "status.probe.down",
            dep = "postgres",
            reason = "no_pool"
        );
        return DepStatus {
            status: "down",
            latency_ms: 0,
        };
    };
    let t0 = Instant::now();
    if sqlx::query("SELECT 1").execute(pool).await.is_ok() {
        DepStatus {
            status: "ok",
            latency_ms: elapsed_ms(t0),
        }
    } else {
        tracing::warn!(
            event = "status.probe.down",
            dep = "postgres",
            reason = "query_failed"
        );
        DepStatus {
            status: "down",
            latency_ms: elapsed_ms(t0),
        }
    }
}

/// Probe GCS: authenticated GET of bucket object listing (AC-1 / plan locked decision 1).
///
/// Free function — NOT a method on `TokenProvider`.
async fn probe_gcs(state: Arc<AppState>) -> DepStatus {
    let t0 = Instant::now();
    let Ok((bearer, _)) = state.gcs_token_provider.get_or_refresh().await else {
        tracing::warn!(
            event = "status.probe.down",
            dep = "gcs",
            reason = "token_failed"
        );
        return DepStatus {
            status: "down",
            latency_ms: elapsed_ms(t0),
        };
    };
    let url = format!(
        "https://storage.googleapis.com/storage/v1/b/{}/o?maxResults=1",
        state.config.gcs_bucket
    );
    probe_http_endpoint(
        HttpProbeArgs {
            http: &state.http,
            url,
            bearer: &bearer,
            dep: "gcs",
        },
        t0,
    )
    .await
}

/// Probe workers: GET `{workers_url}/health` with an ID token (AC-12 mirror of health.rs).
async fn probe_workers(state: Arc<AppState>) -> DepStatus {
    let t0 = Instant::now();
    let Ok(id_token) = state.workers_id_token_provider.fetch_id_token().await else {
        tracing::warn!(
            event = "status.probe.down",
            dep = "workers",
            reason = "id_token_failed"
        );
        return DepStatus {
            status: "down",
            latency_ms: elapsed_ms(t0),
        };
    };
    let url = format!("{}/health", state.config.workers_url);
    probe_http_endpoint(
        HttpProbeArgs {
            http: &state.http,
            url,
            bearer: &id_token,
            dep: "workers",
        },
        t0,
    )
    .await
}

/// Arguments for `probe_http_endpoint` (keeps the call-site ≤4 params).
struct HttpProbeArgs<'a> {
    http: &'a reqwest::Client,
    url: String,
    bearer: &'a secrecy::SecretString,
    dep: &'static str,
}

/// Shared HTTP GET helper used by both GCS and workers probes.
///
/// Sends an authenticated GET, returns `ok` on `2xx`, `down` on any error or
/// non-`2xx` status.  Secrets are exposed only at the `.bearer_auth(…)` call
/// site (secret-hygiene rule).
async fn probe_http_endpoint(args: HttpProbeArgs<'_>, t0: Instant) -> DepStatus {
    match args
        .http
        .get(args.url)
        .bearer_auth(secrecy::ExposeSecret::expose_secret(args.bearer))
        .send()
        .await
    {
        Ok(r) if r.status().is_success() => DepStatus {
            status: "ok",
            latency_ms: elapsed_ms(t0),
        },
        _ => {
            tracing::warn!(
                event = "status.probe.down",
                dep = args.dep,
                reason = "request_failed"
            );
            DepStatus {
                status: "down",
                latency_ms: elapsed_ms(t0),
            }
        }
    }
}

/// Compute root aggregate status (AC-3).
///
/// Returns `"ok"` iff all three dependencies are `"ok"`, else `"degraded"`.
#[must_use]
pub fn aggregate_status(
    postgres: &DepStatus,
    gcs: &DepStatus,
    workers: &DepStatus,
) -> &'static str {
    if postgres.status == "ok" && gcs.status == "ok" && workers.status == "ok" {
        "ok"
    } else {
        "degraded"
    }
}

/// Convert an `Instant` start time to elapsed milliseconds (saturating cast).
fn elapsed_ms(t0: Instant) -> u64 {
    u64::try_from(t0.elapsed().as_millis()).unwrap_or(u64::MAX)
}
