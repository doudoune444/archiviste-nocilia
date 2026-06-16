//! Owner-scoped conversation history endpoints (HIST-001) + GCS signed URL.
//!
//! Three routes, all resolving the caller via the `AnonIdentity` extension
//! (set by `resolve_identity` middleware) so anonymous visitors reach them:
//!   - `GET /v1/conversations` — list the caller's own conversations.
//!   - `GET /v1/conversations/{id}/messages` — turns of an owned conversation,
//!     read directly from the structured `conversation_messages` store (MEM-001).
//!   - `GET /v1/conversations/{id}/signed-url` — GCS V4 signed URL; owner-or-author
//!     (authors moderate any conversation, every other tier only their own).
//!
//! Ownership is enforced in SQL on every read so a caller passing another user's
//! `conversation_id` learns nothing (security.md A01 — IDOR). The two list/read
//! queries carry a `LIMIT` (no unbounded bulk endpoint, security.md A04).
//!
//! Uses `sqlx::query_as` (runtime-typed) to avoid compile-time DB requirement (blockers.md MED-3).

use std::sync::Arc;

use axum::{
    extract::{Extension, Path, State},
    Json,
};
use chrono::Utc;
use serde::Serialize;
use sqlx::FromRow;
use uuid::Uuid;

use crate::{
    auth::extractor::{AnonIdentity, UserTier},
    errors::ApiError,
    gcs::sign,
    state::AppState,
    RequestId,
};

/// Max conversations returned by the owner-scoped list (security.md A04 LIMIT guard).
const MAX_CONVERSATIONS: i64 = 50;

/// Max turns returned when reopening a conversation (security.md A04 LIMIT guard).
const MAX_MESSAGES: i64 = 500;

// ---------------------------------------------------------------------------
// GET /v1/conversations — owner-scoped list
// ---------------------------------------------------------------------------

/// One conversation summary row for the owner-scoped list.
#[derive(Debug, Serialize, FromRow)]
struct ConversationSummary {
    id: Uuid,
    created_at: chrono::DateTime<Utc>,
    updated_at: chrono::DateTime<Utc>,
    message_count: i32,
}

/// Response body for `GET /v1/conversations`.
#[derive(Debug, Serialize)]
pub struct ConversationListResponse {
    conversations: Vec<ConversationSummary>,
}

/// Handler: `GET /v1/conversations` — list the caller's own conversations.
///
/// Owner-scoped via `WHERE user_id = caller`; newest-activity first, capped at
/// `MAX_CONVERSATIONS`. The caller identity (anonymous cookie `UUIDv5`, or member/
/// author JWT `sub`) comes from the `AnonIdentity` extension.
///
/// # Errors
/// `ApiError::UpstreamUnavailable` if no DB pool is configured or the query fails.
pub async fn list_conversations(
    Extension(identity): Extension<AnonIdentity>,
    Extension(req_id): Extension<RequestId>,
    State(state): State<Arc<AppState>>,
) -> Result<Json<ConversationListResponse>, ApiError> {
    let pool = state
        .db_pool
        .as_ref()
        .ok_or(ApiError::UpstreamUnavailable)?;

    let conversations: Vec<ConversationSummary> = sqlx::query_as(
        "SELECT id, created_at, updated_at, message_count \
         FROM conversations WHERE user_id = $1 \
         ORDER BY updated_at DESC LIMIT $2",
    )
    .bind(identity.user_id)
    .bind(MAX_CONVERSATIONS)
    .fetch_all(pool)
    .await
    .map_err(|_| ApiError::UpstreamUnavailable)?;

    tracing::info!(
        event = "conversations.list",
        request_id = %req_id.0,
        user_id = %identity.user_id,
        tier = identity.tier.as_str(),
        count = conversations.len(),
    );

    Ok(Json(ConversationListResponse { conversations }))
}

// ---------------------------------------------------------------------------
// GET /v1/conversations/{id}/messages — owner-scoped turns read
// ---------------------------------------------------------------------------

/// One turn row from `conversation_messages`.
#[derive(Debug, Serialize, FromRow)]
struct ConversationMessage {
    role: String,
    ordinal: i32,
    content: String,
}

/// Response body for `GET /v1/conversations/{id}/messages`.
#[derive(Debug, Serialize)]
pub struct ConversationMessagesResponse {
    conversation_id: Uuid,
    messages: Vec<ConversationMessage>,
}

/// Handler: `GET /v1/conversations/{id}/messages` — turns of an owned conversation.
///
/// Reads the structured `conversation_messages` store directly (AFK — no workers
/// call, no contract change). The `JOIN conversations` enforces ownership in SQL,
/// so a caller passing another user's `conversation_id` gets zero rows.
///
/// # Errors
/// `ApiError::UpstreamUnavailable` if no DB pool is configured or the query fails.
/// `ApiError::ConversationNotFound` if the conversation is not the caller's or does
/// not exist (both collapse to 404 so a non-owner cannot probe existence).
pub async fn conversation_messages(
    Extension(identity): Extension<AnonIdentity>,
    Extension(req_id): Extension<RequestId>,
    Path(conversation_id): Path<Uuid>,
    State(state): State<Arc<AppState>>,
) -> Result<Json<ConversationMessagesResponse>, ApiError> {
    let pool = state
        .db_pool
        .as_ref()
        .ok_or(ApiError::UpstreamUnavailable)?;

    // Ownership enforced in SQL (JOIN on conversations.user_id = caller): a caller
    // passing another user's conversation_id gets zero rows (security.md A01 IDOR).
    let messages: Vec<ConversationMessage> = sqlx::query_as(
        "SELECT cm.role, cm.ordinal, cm.content \
         FROM conversation_messages cm \
         JOIN conversations c ON c.id = cm.conversation_id \
         WHERE cm.conversation_id = $1 AND c.user_id = $2 \
         ORDER BY cm.ordinal ASC LIMIT $3",
    )
    .bind(conversation_id)
    .bind(identity.user_id)
    .bind(MAX_MESSAGES)
    .fetch_all(pool)
    .await
    .map_err(|_| ApiError::UpstreamUnavailable)?;

    // Empty ⇒ not the caller's conversation or non-existent. 404 in both cases so a
    // non-owner cannot distinguish them; an owned conversation always has ≥1 turn.
    if messages.is_empty() {
        return Err(ApiError::ConversationNotFound);
    }

    tracing::info!(
        event = "conversations.messages",
        request_id = %req_id.0,
        user_id = %identity.user_id,
        tier = identity.tier.as_str(),
        conversation_id = %conversation_id,
        count = messages.len(),
    );

    Ok(Json(ConversationMessagesResponse {
        conversation_id,
        messages,
    }))
}

// ---------------------------------------------------------------------------
// GET /v1/conversations/{id}/signed-url — owner-or-author GCS signed URL
// ---------------------------------------------------------------------------

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

/// Handler: `GET /v1/conversations/{id}/signed-url` — owner-or-author signed URL
/// (HIST-001; supersedes the author-only UI-002 gate AC-8..AC-10).
///
/// Identity comes from the `AnonIdentity` extension. Authors may read any
/// conversation's signed URL (moderation dashboard); every other tier is
/// restricted to conversations they own — closing the signed-url IDOR.
///
/// # Errors
/// `ApiError::UpstreamUnavailable` if no DB pool is configured, the DB query fails,
/// or GCS signing fails (Failure modes).
/// `ApiError::ConversationNotFound` if the conversation is not visible to the caller
/// (not found, or not owned by a non-author — AC-10 sub-case a).
pub async fn signed_url(
    Extension(identity): Extension<AnonIdentity>,
    Extension(req_id): Extension<RequestId>,
    Path(conversation_id): Path<Uuid>,
    State(state): State<Arc<AppState>>,
) -> Result<Json<SignedUrlResponse>, ApiError> {
    let request_id = &req_id.0;

    let pool = state
        .db_pool
        .as_ref()
        .ok_or(ApiError::UpstreamUnavailable)?;
    let start = std::time::Instant::now();

    let gcs_uri = fetch_gcs_uri_for_caller(pool, conversation_id, &identity)
        .await
        .map_err(|_| ApiError::UpstreamUnavailable)?
        // AC-10 sub-case a: not found, or not owned by a non-author → 404.
        .ok_or(ApiError::ConversationNotFound)?;

    // Strip `gs://<bucket>/` prefix to get the object path within the bucket.
    let object = gcs_uri_to_object_path(&gcs_uri, &state.config.gcs_bucket)
        .ok_or(ApiError::UpstreamUnavailable)?;

    let now = Utc::now();
    // AC-9: TTL = SIGNED_URL_TTL_SECONDS = 300 s, method = GET strictly.
    // AC-12: sign_get takes &TokenProvider (no SA private key in config — SEC-004).
    let url = sign::sign_get(
        &state.gcs_token_provider,
        &state.config.gcs_signing_sa_email,
        &state.config.gcs_bucket,
        object,
        now,
        None,
    )
    .await
    .map_err(|e| {
        // AC-5: failure log — reason_code ∈ {6 codes}, never log token/signed_blob/string_to_sign.
        tracing::warn!(
            event = "dashboard.signing_failed",
            request_id = %request_id,
            user_id = %identity.user_id,
            latency_ms = u64::try_from(start.elapsed().as_millis()).unwrap_or(u64::MAX),
            reason_code = e.reason_code(),
        );
        ApiError::UpstreamUnavailable
    })?;

    let expires_at =
        now + chrono::Duration::seconds(i64::try_from(sign::SIGNED_URL_TTL_SECONDS).unwrap_or(300));

    let latency_ms = u64::try_from(start.elapsed().as_millis()).unwrap_or(u64::MAX);

    // AC-23: structured log — never log signed_url, gcs_uri (security.md §A09).
    tracing::info!(
        event = "dashboard.conversation.signed_url",
        request_id = %request_id,
        user_id = %identity.user_id,
        tier = identity.tier.as_str(),
        latency_ms,
        conversation_id = %conversation_id,
    );

    Ok(Json(SignedUrlResponse {
        signed_url: url,
        expires_at,
        conversation_id,
    }))
}

/// Fetch a conversation's `gcs_uri`, owner-scoped unless the caller is an author.
///
/// Authors (moderation dashboard) read any conversation; every other tier is
/// restricted to conversations they own, closing the signed-url IDOR (HIST-001).
async fn fetch_gcs_uri_for_caller(
    pool: &sqlx::PgPool,
    conversation_id: Uuid,
    identity: &AnonIdentity,
) -> Result<Option<String>, sqlx::Error> {
    let row: Option<ConversationGcsRow> = if identity.tier == UserTier::Author {
        sqlx::query_as("SELECT gcs_uri FROM conversations WHERE id = $1")
            .bind(conversation_id)
            .fetch_optional(pool)
            .await?
    } else {
        sqlx::query_as("SELECT gcs_uri FROM conversations WHERE id = $1 AND user_id = $2")
            .bind(conversation_id)
            .bind(identity.user_id)
            .fetch_optional(pool)
            .await?
    };
    Ok(row.map(|r| r.gcs_uri))
}

/// Strip `gs://<bucket>/` prefix from a `gcs_uri`, returning the object path within the bucket.
fn gcs_uri_to_object_path<'a>(gcs_uri: &'a str, bucket: &str) -> Option<&'a str> {
    gcs_uri.strip_prefix(&format!("gs://{bucket}/"))
}
