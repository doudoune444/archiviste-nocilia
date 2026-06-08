//! In-memory snapshot cache for `GET /v1/status` (OBS-002 AC-10 / D-3).
//!
//! Mirrors the read-lock fast-path / write-lock double-check pattern from
//! `auth_metadata::token::TokenProvider`.
//!
//! `HealthSnapshotCache` holds a single `Option<CachedSnapshot>` behind an
//! `RwLock`.  Readers acquire the read-lock and return the cached value if it
//! is still fresh (< `SNAPSHOT_TTL`).  On a cache miss the write-lock is
//! acquired, double-checked, and the probe closure is executed to compute a
//! fresh snapshot.
//!
//! `checked_at` lives INSIDE the cached value so callers always get the
//! timestamp of the snapshot's production time, not the time of the HTTP
//! request (AC-4).

use chrono::{DateTime, Utc};
use std::time::Duration;
use tokio::sync::RwLock;

use crate::handlers::status::StatusResponse;

/// TTL for the in-memory snapshot cache (AC-10 / D-3).
pub const SNAPSHOT_TTL: Duration = Duration::from_secs(10);

/// A single cached status snapshot with its production timestamp.
struct CachedSnapshot {
    snapshot: StatusResponse,
    checked_at: DateTime<Utc>,
}

/// Process-wide in-memory snapshot cache (AC-10).
///
/// Wrap in `Arc<HealthSnapshotCache>` and store on `AppState.health_cache`.
pub struct HealthSnapshotCache {
    inner: RwLock<Option<CachedSnapshot>>,
}

impl HealthSnapshotCache {
    /// Create a new, empty cache.
    #[must_use]
    pub fn new() -> Self {
        Self {
            inner: RwLock::new(None),
        }
    }

    /// Return a fresh or cached `StatusResponse`.
    ///
    /// Fast path: if a cached snapshot is < `SNAPSHOT_TTL` old, clone and return it
    /// immediately under read-lock (AC-10 hot path, no probe I/O).
    ///
    /// Slow path: acquire write-lock, double-check, then run `probe_fn` to produce
    /// a fresh snapshot.  `checked_at` is recorded at the start of the probe run.
    pub async fn get_or_refresh<F, Fut>(&self, probe_fn: F) -> StatusResponse
    where
        F: FnOnce() -> Fut,
        Fut: std::future::Future<Output = StatusResponse>,
    {
        // Fast path under read-lock.
        {
            let guard = self.inner.read().await;
            if let Some(cached) = guard.as_ref() {
                if is_fresh(cached.checked_at) {
                    return cached.snapshot.clone();
                }
            }
        }
        // Slow path under write-lock + double-check.
        let mut guard = self.inner.write().await;
        if let Some(cached) = guard.as_ref() {
            if is_fresh(cached.checked_at) {
                return cached.snapshot.clone();
            }
        }
        // Record production time before probes run so it is stable (AC-4).
        let checked_at = Utc::now();
        let mut snapshot = probe_fn().await;
        // Overwrite the checked_at in the snapshot with the pre-probe timestamp.
        snapshot.checked_at = checked_at.to_rfc3339();
        *guard = Some(CachedSnapshot {
            snapshot: snapshot.clone(),
            checked_at,
        });
        snapshot
    }
}

/// Returns `true` when `t` is recent enough to be served from cache.
fn is_fresh(t: DateTime<Utc>) -> bool {
    let age = Utc::now()
        .signed_duration_since(t)
        .to_std()
        .unwrap_or(Duration::MAX);
    age < SNAPSHOT_TTL
}

impl Default for HealthSnapshotCache {
    fn default() -> Self {
        Self::new()
    }
}
