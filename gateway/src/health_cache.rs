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

#[cfg(test)]
impl HealthSnapshotCache {
    /// Force-age the cached snapshot so that the next `get_or_refresh` treats it as expired.
    ///
    /// Inserts `snapshot` with a `checked_at` older than `SNAPSHOT_TTL`, enabling
    /// deterministic TTL-expiry tests without real wall-clock sleeps (AC-10).
    ///
    /// The offset (11 s) is chosen to exceed `SNAPSHOT_TTL` (10 s) by 1 s; no panic
    /// is possible because `chrono::Duration::seconds` never fails for this literal.
    pub async fn inject_expired(&self, snapshot: StatusResponse) {
        // 11 s > SNAPSHOT_TTL (10 s): guaranteed to be stale on the very next read.
        let stale_checked_at = Utc::now() - chrono::Duration::seconds(11);
        let mut guard = self.inner.write().await;
        *guard = Some(CachedSnapshot {
            snapshot,
            checked_at: stale_checked_at,
        });
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
#[allow(clippy::expect_used)]
mod tests {
    use std::sync::{
        atomic::{AtomicUsize, Ordering},
        Arc,
    };

    use super::*;
    use crate::handlers::status::{DepStatus, Dependencies, StatusResponse};

    fn make_snapshot(tag: &str) -> StatusResponse {
        StatusResponse {
            status: "ok",
            dependencies: Dependencies {
                postgres: DepStatus {
                    status: "ok",
                    latency_ms: 1,
                },
                gcs: DepStatus {
                    status: "ok",
                    latency_ms: 1,
                },
                workers: DepStatus {
                    status: "ok",
                    latency_ms: 1,
                },
            },
            // Tag is embedded in checked_at so tests can distinguish snapshots.
            checked_at: tag.to_string(),
        }
    }

    /// AC-10: two `get_or_refresh` calls within TTL invoke the probe closure exactly ONCE.
    ///
    /// Asserts counter == 1 after two calls and that both calls return the same `checked_at`.
    #[tokio::test]
    async fn ac10_cache_prevents_second_probe_within_ttl() {
        // AC-10: probe counter must stay at 1 for two calls within the 10s TTL.
        let counter = Arc::new(AtomicUsize::new(0));
        let cache = HealthSnapshotCache::new();

        let counter_a = Arc::clone(&counter);
        let snap1 = cache
            .get_or_refresh(|| async move {
                counter_a.fetch_add(1, Ordering::SeqCst);
                make_snapshot("first")
            })
            .await;

        let counter_b = Arc::clone(&counter);
        let snap2 = cache
            .get_or_refresh(|| async move {
                counter_b.fetch_add(1, Ordering::SeqCst);
                make_snapshot("second")
            })
            .await;

        assert_eq!(
            counter.load(Ordering::SeqCst),
            1,
            "AC-10: probe closure must be invoked exactly once within TTL"
        );
        // Both calls serve the same snapshot (same checked_at set by get_or_refresh).
        assert_eq!(
            snap1.checked_at, snap2.checked_at,
            "AC-10: checked_at must be identical for two calls within TTL"
        );
    }

    /// AC-10: after TTL expiry, the next `get_or_refresh` produces a fresh, distinct `checked_at`.
    ///
    /// Uses `inject_expired` to avoid a real 10-second sleep.
    #[tokio::test]
    async fn ac10_cache_refreshes_after_ttl_expiry() {
        // AC-10: expired entry → get_or_refresh must produce a distinct checked_at.
        let cache = HealthSnapshotCache::new();
        let stale = make_snapshot("stale");
        let stale_checked_at = stale.checked_at.clone();
        // Artificially age the cache entry past TTL (no real sleep needed).
        cache.inject_expired(stale).await;

        let fresh = cache
            .get_or_refresh(|| async { make_snapshot("irrelevant") })
            .await;

        assert_ne!(
            fresh.checked_at, stale_checked_at,
            "AC-10: checked_at after TTL expiry must differ from stale value"
        );
        // The new checked_at must parse as valid RFC3339.
        chrono::DateTime::parse_from_rfc3339(&fresh.checked_at)
            .expect("AC-10: fresh checked_at must be valid RFC3339");
    }
}
