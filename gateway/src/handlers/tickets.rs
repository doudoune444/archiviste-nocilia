//! `GET /v1/tickets` — paginated list of open lore-gap tickets (AC-5, AC-6, AC-7, AC-20, AC-23).
//!
//! Uses `sqlx::query_as` (runtime-typed) to avoid the offline-cache requirement of
//! `sqlx::query!` macros when no live DB is present at compile time (blockers.md MED-3).
//! The SQL is byte-for-byte as required by AC-20.

use std::sync::Arc;

use axum::{
    extract::{Query, State},
    Json,
};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use sqlx::FromRow;
use uuid::Uuid;

use crate::{
    auth::extractor::{RequireAuthor, UserTier},
    errors::ApiError,
    state::AppState,
};

/// Query params for `GET /v1/tickets` (AC-5, AC-7).
#[derive(Debug, Deserialize)]
pub struct TicketsQuery {
    /// Max items returned. Range `[1, 200]`; default 50 (AC-5, AC-7).
    pub limit: Option<i64>,
    /// Offset for pagination. Must be `≥ 0`; default 0 (AC-5, AC-7).
    pub offset: Option<i64>,
}

/// Single ticket row as returned by the SQL query (AC-5, AC-20).
#[derive(Debug, FromRow)]
struct TicketRow {
    id: Uuid,
    conversation_id: Uuid,
    question: String,
    category: String,
    priority_score: i32,
    status: String,
    created_at: DateTime<Utc>,
    updated_at: DateTime<Utc>,
}

/// Single ticket item in the response (AC-5).
#[derive(Debug, Serialize)]
pub struct TicketItem {
    /// Ticket UUID.
    pub id: Uuid,
    /// Linked conversation UUID.
    pub conversation_id: Uuid,
    /// Raw question text (rendered via `textContent` in UI, never `innerHTML`).
    pub question: String,
    /// Category label.
    pub category: String,
    /// Priority score (higher = more urgent).
    pub priority_score: i32,
    /// Ticket status (always `"open"` in this response — only open tickets returned).
    pub status: String,
    /// Creation timestamp (RFC3339 via serde).
    pub created_at: DateTime<Utc>,
    /// Last-update timestamp (RFC3339 via serde).
    pub updated_at: DateTime<Utc>,
}

/// Response envelope for `GET /v1/tickets` (AC-5).
#[derive(Debug, Serialize)]
pub struct TicketsResponse {
    /// Paginated ticket items.
    pub items: Vec<TicketItem>,
    /// Total count of open tickets (for pagination math).
    pub total: i64,
    /// Effective limit used in this request.
    pub limit: i64,
    /// Effective offset used in this request.
    pub offset: i64,
}

/// Count row for the `SELECT count(*)` query.
#[derive(Debug, FromRow)]
struct CountRow {
    count: i64,
}

/// Handler: `GET /v1/tickets` — author-gated, paginated open tickets (AC-5, AC-6, AC-7).
///
/// # Errors
/// `ApiError::AuthorRequired` if caller is not `author` (AC-6).
/// `ApiError::InvalidRequest` if `limit`/`offset` are out of range (AC-7).
/// `ApiError::UpstreamUnavailable` if DB is unreachable (Failure modes).
pub async fn list_tickets(
    author: Result<RequireAuthor, crate::auth::extractor::AuthError>,
    Query(params): Query<TicketsQuery>,
    State(state): State<Arc<AppState>>,
) -> Result<Json<TicketsResponse>, ApiError> {
    // AC-6: author gate — 403 for non-author (byte-for-byte AC-2 envelope via ApiError).
    let author = author.map_err(|_| ApiError::AuthorRequired)?;

    // AC-7: validate limit ∈ [1, 200] and offset ≥ 0.
    let limit = params.limit.unwrap_or(50);
    let offset = params.offset.unwrap_or(0);
    if !(1..=200).contains(&limit) || offset < 0 {
        return Err(ApiError::InvalidRequest);
    }

    let pool = state
        .db_pool
        .as_ref()
        .ok_or(ApiError::UpstreamUnavailable)?;
    let start = std::time::Instant::now();

    // AC-20: SQL literal — ORDER BY priority_score DESC, created_at DESC, WHERE status='open'.
    // Uses `sqlx::query_as` (runtime-typed) instead of `sqlx::query!` macro to avoid
    // requiring a live DB at compile time (no `.sqlx` offline cache in this repo).
    let rows: Vec<TicketRow> = sqlx::query_as(
        "SELECT id, conversation_id, question, category, priority_score, status, created_at, updated_at \
         FROM tickets \
         WHERE status = 'open' \
         ORDER BY priority_score DESC, created_at DESC \
         LIMIT $1 OFFSET $2",
    )
    .bind(limit)
    .bind(offset)
    .fetch_all(pool)
    .await
    .map_err(|_| ApiError::UpstreamUnavailable)?;

    let count_row: CountRow =
        sqlx::query_as("SELECT count(*) as count FROM tickets WHERE status = 'open'")
            .fetch_one(pool)
            .await
            .map_err(|_| ApiError::UpstreamUnavailable)?;

    let latency_ms = u64::try_from(start.elapsed().as_millis()).unwrap_or(u64::MAX);
    let item_count = rows.len();

    // AC-23: structured log — fields request_id, user_id, tier, latency_ms, count.
    // Never logs `question` (PII, security.md §A09).
    tracing::info!(
        event = "dashboard.tickets.list",
        user_id = %author.0.user_id,
        tier = UserTier::Author.as_str(),
        latency_ms,
        count = item_count,
        limit,
        offset,
    );

    let items = rows
        .into_iter()
        .map(|r| TicketItem {
            id: r.id,
            conversation_id: r.conversation_id,
            question: r.question,
            category: r.category,
            priority_score: r.priority_score,
            status: r.status,
            created_at: r.created_at,
            updated_at: r.updated_at,
        })
        .collect();

    Ok(Json(TicketsResponse {
        items,
        total: count_row.count,
        limit,
        offset,
    }))
}
