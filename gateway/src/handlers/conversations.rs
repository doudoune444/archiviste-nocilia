//! `GET /v1/conversations/{id}/signed-url` — GCS V4 signed URL for a conversation (AC-8..AC-10, AC-23).
//!
//! Uses `sqlx::query_as` (runtime-typed) to avoid compile-time DB requirement (blockers.md MED-3).

use std::sync::Arc;

use axum::{
    extract::{Path, State},
    Json,
};
use chrono::Utc;
use secrecy::ExposeSecret;
use serde::Serialize;
use sqlx::FromRow;
use uuid::Uuid;

use crate::{
    auth::extractor::{RequireAuthor, UserTier},
    errors::ApiError,
    gcs::sign,
    state::AppState,
};

/// Minimal row from `conversations` table: only `gcs_uri` needed.
#[derive(Debug, FromRow)]
struct ConversationGcsRow {
    gcs_uri: String,
}

/// Response body for `GET /v1/conversations/{id}/signed-url` (AC-8).
#[derive(Debug, Serialize)]
pub struct SignedUrlResponse {
    /// HTTPS signed URL for reading the conversation `.md` from GCS.
    pub signed_url: String,
    /// Timestamp when the signed URL expires (RFC3339; `now + 300 s`, AC-9).
    pub expires_at: chrono::DateTime<Utc>,
    /// Echo of the requested conversation UUID.
    pub conversation_id: Uuid,
}

/// Handler: `GET /v1/conversations/{id}/signed-url` — author-gated GCS signed URL (AC-8..AC-10).
///
/// # Errors
/// `ApiError::AuthorRequired` if caller is not `author` (AC-10 sub-case c).
/// `ApiError::ConversationNotFound` if no row matches `id` (AC-10 sub-case a).
/// `ApiError::UpstreamUnavailable` if DB or GCS signing fails (Failure modes).
pub async fn signed_url(
    author: Result<RequireAuthor, crate::auth::extractor::AuthError>,
    Path(conversation_id): Path<Uuid>,
    State(state): State<Arc<AppState>>,
) -> Result<Json<SignedUrlResponse>, ApiError> {
    // AC-10 sub-case c: author gate — 403 byte-for-byte AC-2 envelope.
    let author = author.map_err(|_| ApiError::AuthorRequired)?;

    let pool = state
        .db_pool
        .as_ref()
        .ok_or(ApiError::UpstreamUnavailable)?;
    let start = std::time::Instant::now();

    // AC-8: SELECT gcs_uri from conversations WHERE id = $1.
    let row: Option<ConversationGcsRow> =
        sqlx::query_as("SELECT gcs_uri FROM conversations WHERE id = $1")
            .bind(conversation_id)
            .fetch_optional(pool)
            .await
            .map_err(|_| ApiError::UpstreamUnavailable)?;

    let gcs_uri = match row {
        // AC-10 sub-case a: conversation not found → 404.
        None => return Err(ApiError::ConversationNotFound),
        Some(r) => r.gcs_uri,
    };

    // Strip `gs://<bucket>/` prefix to get the object path within the bucket.
    let object = gcs_uri_to_object_path(&gcs_uri, &state.config.gcs_bucket)
        .ok_or(ApiError::UpstreamUnavailable)?;

    let now = Utc::now();
    // AC-9: TTL = SIGNED_URL_TTL_SECONDS = 300 s, method = GET strictly.
    let url = sign::sign_get(
        &state.config.gcs_signing_sa_email,
        state.config.gcs_signing_private_key_pem.expose_secret(),
        &state.config.gcs_bucket,
        object,
        now,
    )
    .map_err(|e| {
        // Failure mode: SA signing failed — warn log (AC-23), key details never surfaced.
        tracing::warn!(
            event = "dashboard.signing_failed",
            user_id = %author.0.user_id,
            reason = %e,
        );
        ApiError::UpstreamUnavailable
    })?;

    let expires_at =
        now + chrono::Duration::seconds(i64::try_from(sign::SIGNED_URL_TTL_SECONDS).unwrap_or(300));

    let latency_ms = u64::try_from(start.elapsed().as_millis()).unwrap_or(u64::MAX);

    // AC-23: structured log — never log signed_url, gcs_uri (security.md §A09).
    tracing::info!(
        event = "dashboard.conversation.signed_url",
        user_id = %author.0.user_id,
        tier = UserTier::Author.as_str(),
        latency_ms,
        conversation_id = %conversation_id,
    );

    Ok(Json(SignedUrlResponse {
        signed_url: url,
        expires_at,
        conversation_id,
    }))
}

/// Strip `gs://<bucket>/` prefix from a `gcs_uri`, returning the object path within the bucket.
fn gcs_uri_to_object_path<'a>(gcs_uri: &'a str, bucket: &str) -> Option<&'a str> {
    gcs_uri.strip_prefix(&format!("gs://{bucket}/"))
}
