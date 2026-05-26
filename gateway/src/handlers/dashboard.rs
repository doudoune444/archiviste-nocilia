//! `GET /dashboard` — author-gated HTML page (UI-002b AC-1, AC-11).
//!
//! Serves the static `dashboard.html` via `include_str!` so the auth extractor
//! `RequireAuthor` can gate access (a bare `ServeFile` carries no extractor).
//! The global security-header middleware (UI-001 AC-6) covers this route automatically.

use axum::response::Html;

use crate::auth::extractor::RequireAuthor;

/// The dashboard HTML page embedded at compile time (AC-1, R3 plan).
///
/// Using `include_str!` avoids a runtime filesystem read and lets the binary
/// ship a single artifact.  The path is relative to this source file.
const DASHBOARD_HTML: &str = include_str!("../../static/dashboard.html");

/// Handler: `GET /dashboard` — returns the dashboard HTML page for `author` tier.
///
/// `RequireAuthor` returns 403 `author_required` for `member` or anonymous callers
/// (AC-2 / AC-11 / SEC-001 AC-16 byte-for-byte).
pub async fn serve_dashboard(_author: RequireAuthor) -> Html<&'static str> {
    Html(DASHBOARD_HTML)
}
