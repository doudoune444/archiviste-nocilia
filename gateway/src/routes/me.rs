//! Handler for `GET /v1/me` — SEC-001 PR-a (AC-9, AC-10).
//!
//! Public route: returns anonymous identity (fingerprint + `UUIDv5`) or
//! member/author identity (from valid JWT).  No IDOR possible — the route
//! always returns the caller's own identity, never a parameterised one.

use axum::{
    extract::Extension,
    http::StatusCode,
    response::{IntoResponse, Response},
    Json,
};
use serde::Serialize;

use crate::{auth::extractor::AnonIdentity, RequestId};

// ---------------------------------------------------------------------------
// Response body
// ---------------------------------------------------------------------------

/// `GET /v1/me` response body (AC-9).
#[derive(Debug, Serialize)]
pub struct MeResponse {
    /// User UUID (`UUIDv5` fingerprint for anonymous; JWT sub for members/authors).
    pub user_id: String,
    /// Tier: `"anonymous"`, `"member"`, or `"author"`.
    pub tier: &'static str,
    /// SHA-256 hex fingerprint (64 chars) for anonymous; `null` for authenticated.
    pub fingerprint: Option<String>,
}

// ---------------------------------------------------------------------------
// Handler
// ---------------------------------------------------------------------------

/// Handler for `GET /v1/me`.
///
/// The `AnonIdentity` extension is set by the `resolve_identity` middleware
/// before this handler runs. Authenticated callers have `tier=member|author`
/// and `fingerprint=None`; anonymous callers have `fingerprint=Some(hex)`.
pub async fn me(
    Extension(identity): Extension<AnonIdentity>,
    Extension(_req_id): Extension<RequestId>,
) -> Response {
    let body = MeResponse {
        user_id: identity.user_id.to_string(),
        tier: identity.tier.as_str(),
        fingerprint: identity.fingerprint.clone(),
    };
    (StatusCode::OK, Json(body)).into_response()
}
