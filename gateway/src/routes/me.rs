//! Handler for `GET /v1/me` — SEC-001 PR-a (AC-9) / IDN-001 (cookie-dominant identity).
//! PLATFORM-003: adds `email` field for member/author tiers (DB lookup via `UserLookup`).
//!
//! Public route: returns anonymous identity (`user_id` + cookie UUID) or
//! member/author identity (from valid JWT).  No IDOR possible — the route
//! always returns the caller's own identity, never a parameterised one.

use std::sync::Arc;

use axum::{
    extract::{Extension, State},
    http::StatusCode,
    response::{IntoResponse, Response},
    Json,
};
use serde::Serialize;

use crate::{
    auth::extractor::{AnonIdentity, UserTier},
    state::AppState,
    RequestId,
};

// ---------------------------------------------------------------------------
// Response body
// ---------------------------------------------------------------------------

/// `GET /v1/me` response body (AC-9 / PLATFORM-003).
#[derive(Serialize)]
pub struct MeResponse {
    /// User UUID (`UUIDv5` fingerprint for anonymous; JWT sub for members/authors).
    pub user_id: String,
    /// Tier: `"anonymous"`, `"member"`, or `"author"`.
    pub tier: &'static str,
    /// Cookie UUID (`archiviste_anon`) for anonymous callers; `null` for authenticated.
    ///
    /// IDN-001: the `user_id` is derived solely from this cookie value.
    pub fingerprint: Option<String>,
    /// Email address for member/author callers; `null` for anonymous.
    ///
    /// PLATFORM-003: populated only when `tier != anonymous`.
    /// A09: this field is NEVER written to any log or tracing span.
    pub email: Option<String>,
}

// ---------------------------------------------------------------------------
// Handler
// ---------------------------------------------------------------------------

/// Handler for `GET /v1/me`.
///
/// The `AnonIdentity` extension is set by the `resolve_identity` middleware
/// before this handler runs. Authenticated callers have `tier=member|author`
/// and `fingerprint=None`; anonymous callers have `fingerprint=Some(cookie_uuid)`.
///
/// PLATFORM-003: for member/author callers the email is fetched from the DB via
/// `state.user_lookup.find_email_by_id`.  The lookup is fail-soft: if
/// `user_lookup` is `None` (test env without a DB) or the query errors, the
/// response still carries a valid identity with `email: None`.  Identity must
/// always resolve — a DB hiccup on the email lookup must never 500 this route.
pub async fn me(
    Extension(identity): Extension<AnonIdentity>,
    Extension(_req_id): Extension<RequestId>,
    State(state): State<Arc<AppState>>,
) -> Response {
    let email = resolve_email(&identity, &state).await;

    let body = MeResponse {
        user_id: identity.user_id.to_string(),
        tier: identity.tier.as_str(),
        fingerprint: identity.fingerprint.clone(),
        email,
    };
    (StatusCode::OK, Json(body)).into_response()
}

/// Fetch email for authenticated callers; returns `None` on any failure.
///
/// PLATFORM-003 fail-soft: `user_lookup` absent, DB unavailable, or user row
/// missing all degrade to `None` without surfacing an error to the caller.
async fn resolve_email(identity: &AnonIdentity, state: &AppState) -> Option<String> {
    if identity.tier == UserTier::Anonymous {
        return None;
    }

    let lookup = state.user_lookup.as_ref()?;

    // Fail-soft: a lookup error degrades to email=None, never 500s.
    lookup
        .find_email_by_id(identity.user_id)
        .await
        .unwrap_or(None)
}
