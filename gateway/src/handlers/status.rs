//! `GET /v1/status` — public anonymous endpoint returning aggregate service health.
//!
//! AC-1: body `{status, dependencies: {postgres, gcs, workers}, checked_at}`.
//! AC-2: each dep = `{status, latency_ms}` — closed enum, no detail leak.
//!   postgres/gcs status ∈ `{ok, down}`; workers status ∈ `{ok, dormant, down}` (#253).
//! AC-3: root `ok` ⟺ every dep ∈ `{ok, dormant}`; any `down` ⟹ `degraded` (#253).
//! AC-5/AC-6: `200` always — no `5xx` path, no `503`.
//! AC-7: no auth extractor — mounted in `public_api`.
//! AC-8: `PROBE_TIMEOUT` 2 s per probe via `tokio::time::timeout`.
//! AC-9: 3 probes run via `tokio::join!` (parallel).
//! AC-10: result cached via `HealthSnapshotCache` (10 s TTL, in `AppState`).
//! AC-11: probe error → `down` + `tracing::warn`; no detail in body.
//! #253: the workers probe reads the Cloud Run Admin `Ready` condition out-of-band
//!   (`Ready=True → dormant`, `Ready=False → down`); it never calls `{workers}/health`,
//!   so it cannot wake the scale-to-zero service.

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
    /// `"ok"`, `"dormant"`, or `"down"` — closed enum (AC-2 / #253).
    ///
    /// `"dormant"` is emitted ONLY by the workers probe (scale-to-zero: ready to
    /// serve but cold).  Postgres and GCS emit only `"ok"` / `"down"`.
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

/// Probe workers out-of-band via the Cloud Run Admin `Ready` condition (#253).
///
/// Reads the latest-revision `Ready` condition WITHOUT calling `{workers}/health`,
/// so the scale-to-zero service is never woken:
/// - `Ready=True`  → `dormant` (ready to serve, possibly cold — healthy, never red).
/// - `Ready=False` → `down` (deployment really broken).
/// - token fetch / request / parse failure → `down` (safe default, no detail leak).
async fn probe_workers(state: Arc<AppState>) -> DepStatus {
    let t0 = Instant::now();
    let Ok((bearer, _)) = state.run_token_provider.get_or_refresh().await else {
        tracing::warn!(
            event = "status.probe.down",
            dep = "workers",
            reason = "run_token_failed"
        );
        return DepStatus {
            status: "down",
            latency_ms: elapsed_ms(t0),
        };
    };

    let Some(body) = fetch_cloud_run_service(&state, &bearer).await else {
        tracing::warn!(
            event = "status.probe.down",
            dep = "workers",
            reason = "cloud_run_unreachable"
        );
        return DepStatus {
            status: "down",
            latency_ms: elapsed_ms(t0),
        };
    };

    let status = workers_status_from_ready(&body);
    if status == "down" {
        tracing::warn!(
            event = "status.probe.down",
            dep = "workers",
            reason = "not_ready"
        );
    }
    DepStatus {
        status,
        latency_ms: elapsed_ms(t0),
    }
}

/// GET the Cloud Run service descriptor; returns the parsed JSON, or `None` on any failure.
async fn fetch_cloud_run_service(
    state: &AppState,
    bearer: &secrecy::SecretString,
) -> Option<serde_json::Value> {
    let response = state
        .http
        .get(&state.config.cloud_run_service_url)
        .bearer_auth(secrecy::ExposeSecret::expose_secret(bearer))
        .send()
        .await
        .ok()?;
    if !response.status().is_success() {
        return None;
    }
    response.json::<serde_json::Value>().await.ok()
}

/// Map a Cloud Run service descriptor's `Ready` condition to a workers dep status.
///
/// `Ready=CONDITION_SUCCEEDED` → `dormant` (healthy); anything else → `down`.
/// The `Ready` condition is looked up in `conditions[]` first, then `terminalCondition`.
fn workers_status_from_ready(service: &serde_json::Value) -> &'static str {
    let ready_state = ready_condition_state(service);
    if ready_state == Some("CONDITION_SUCCEEDED") {
        "dormant"
    } else {
        "down"
    }
}

/// Extract the `state` of the `Ready` condition from a Cloud Run v2 service descriptor.
fn ready_condition_state(service: &serde_json::Value) -> Option<&str> {
    let from_array = service
        .get("conditions")
        .and_then(serde_json::Value::as_array)
        .and_then(|conditions| {
            conditions
                .iter()
                .find(|c| c.get("type").and_then(serde_json::Value::as_str) == Some("Ready"))
        });
    let condition = from_array.or_else(|| {
        service
            .get("terminalCondition")
            .filter(|c| c.get("type").and_then(serde_json::Value::as_str) == Some("Ready"))
    })?;
    condition.get("state").and_then(serde_json::Value::as_str)
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

/// Compute root aggregate status (AC-3 / #253).
///
/// Returns `"ok"` iff every dependency is healthy — `"ok"` OR `"dormant"` — else
/// `"degraded"`.  A `"dormant"` Workers (scale-to-zero, Ready=True) is the nominal
/// state and never degrades the root; only a real `"down"` does.
#[must_use]
pub fn aggregate_status(
    postgres: &DepStatus,
    gcs: &DepStatus,
    workers: &DepStatus,
) -> &'static str {
    if is_healthy(postgres) && is_healthy(gcs) && is_healthy(workers) {
        "ok"
    } else {
        "degraded"
    }
}

/// A dependency is healthy when it is serving (`ok`) or ready-but-cold (`dormant`).
fn is_healthy(dep: &DepStatus) -> bool {
    dep.status == "ok" || dep.status == "dormant"
}

/// Convert an `Instant` start time to elapsed milliseconds (saturating cast).
fn elapsed_ms(t0: Instant) -> u64 {
    u64::try_from(t0.elapsed().as_millis()).unwrap_or(u64::MAX)
}
