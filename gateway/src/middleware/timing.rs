//! Axum middleware that appends `X-Gateway-Overhead-Ms` to every response.
//!
//! # Design (OPS-001a plan §D-H2)
//! - Inserts a shared atomic `WorkersCallDuration` slot into request extensions.
//! - Calls the inner service via `next.run(req)`.
//! - The chat handler writes the workers call duration (nanoseconds) atomically.
//! - After the inner service returns, reads the slot value.
//! - `overhead = total − workers_call` (or `total` when the slot is zero,
//!   e.g. validation-failure 400 that never called workers — plan §39).
//! - Inserts `X-Gateway-Overhead-Ms: <u64 ms>` into the response headers.
//!
//! Using a request-extension atomic cell avoids relying on response-extension
//! propagation across Axum's `MapIntoResponse` wrappers.

use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

use axum::{extract::Request, http::HeaderValue, middleware::Next, response::Response};

// ---------------------------------------------------------------------------
// Shared atomic slot written by the chat handler, read by this middleware
// ---------------------------------------------------------------------------

/// Shared atomic slot inserted into request extensions by `overhead_header`.
///
/// The chat handler writes the workers call duration (nanoseconds) after
/// awaiting the workers HTTP request. Zero means "not written yet".
#[derive(Clone)]
pub struct WorkersCallDuration(pub Arc<AtomicU64>);

impl WorkersCallDuration {
    /// Create a new zero-initialised slot.
    #[must_use]
    pub fn new() -> Self {
        Self(Arc::new(AtomicU64::new(0)))
    }

    /// Write the workers call duration (called by the chat handler).
    pub fn set(&self, duration: Duration) {
        // Truncate to u64 nanoseconds; saturate on overflow (> 584 years → irrelevant).
        let nanos = u64::try_from(duration.as_nanos()).unwrap_or(u64::MAX);
        self.0.store(nanos, Ordering::Release);
    }

    /// Read the workers call duration; returns `Duration::ZERO` if not written.
    #[must_use]
    pub fn get(&self) -> Duration {
        let nanos = self.0.load(Ordering::Acquire);
        Duration::from_nanos(nanos)
    }
}

impl Default for WorkersCallDuration {
    fn default() -> Self {
        Self::new()
    }
}

// ---------------------------------------------------------------------------
// Header name constant
// ---------------------------------------------------------------------------

/// Response header name exposed to k6 and monitoring (OPS-001 AC-4).
const OVERHEAD_HEADER: &str = "x-gateway-overhead-ms";

// ---------------------------------------------------------------------------
// Middleware function (used with axum::middleware::from_fn)
// ---------------------------------------------------------------------------

/// Axum middleware that measures gateway-only overhead and inserts
/// `X-Gateway-Overhead-Ms` into every response.
///
/// Wire via `Router::layer(axum::middleware::from_fn(overhead_header))`.
pub async fn overhead_header(mut req: Request, next: Next) -> Response {
    // Insert shared atomic slot into request extensions so the chat handler can write to it.
    let slot = WorkersCallDuration::new();
    req.extensions_mut().insert(slot.clone());

    let start = Instant::now();
    let mut resp = next.run(req).await;
    let total = start.elapsed();

    // Read workers call duration; zero if the handler never populated the slot.
    let workers = slot.get();
    let overhead = total.saturating_sub(workers);
    let overhead_ms = u64::try_from(overhead.as_millis()).unwrap_or(u64::MAX);

    if let Ok(hv) = HeaderValue::from_str(&overhead_ms.to_string()) {
        resp.headers_mut().insert(OVERHEAD_HEADER, hv);
    }

    resp
}
