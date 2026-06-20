//! `GET /v1/tickets` — paginated list of open lore-gap tickets (AC-5, AC-6, AC-7, AC-20, AC-23).
//!
//! Uses `sqlx::query_as` (runtime-typed) to avoid the offline-cache requirement of
//! `sqlx::query!` macros when no live DB is present at compile time (blockers.md MED-3).
//! The SQL is byte-for-byte as required by AC-20.
//!
//! DASH-001 fix: optional `category` filter and `sort=priority|date` added, mirroring
//! the board endpoint's filter/sort (board.rs). Sort is represented as a Rust enum —
//! never interpolated into SQL strings. Four separate `sqlx::query_as` literal calls
//! carry the four (category × sort) combinations; no `format!` is ever used
//! (security.md A03 — injection prevention).

use std::sync::Arc;

use axum::{
    extract::{Extension, Query, State},
    Json,
};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use sqlx::FromRow;
use uuid::Uuid;

use crate::{
    auth::extractor::{RequireAuthor, UserTier},
    errors::ApiError,
    handlers::board::SortOrder,
    state::AppState,
    RequestId,
};

/// Query params for `GET /v1/tickets` (AC-5, AC-7, DASH-001).
#[derive(Debug, Deserialize)]
pub struct TicketsQuery {
    /// Max items returned. Range `[1, 200]`; default 50 (AC-5, AC-7).
    pub limit: Option<i64>,
    /// Offset for pagination. Must be `≥ 0`; default 0 (AC-5, AC-7).
    pub offset: Option<i64>,
    /// Optional category filter. When present, only tickets whose `category` column
    /// matches exactly are returned. Value is passed as a bound parameter — never
    /// interpolated into SQL (security.md A03).
    pub category: Option<String>,
    /// Sort order: `priority` (default) or `date`.
    /// Unknown values silently fall back to the default (backward-compatible).
    #[serde(default)]
    pub sort: SortOrder,
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
    /// Human override flag — judges did not confirm this ticket (#163).
    judges_not_passed: bool,
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
    /// True when raised via human "send anyway" override — judges did not confirm (#163).
    pub judges_not_passed: bool,
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

/// Handler: `GET /v1/tickets` — author-gated, paginated open tickets (AC-5, AC-6, AC-7, DASH-001).
///
/// Uses `RequireAuthor` directly (without `Result<>`) so that Axum lets
/// `AuthError::IntoResponse` produce the correct status codes:
/// `InvalidToken`/`SessionRevoked` → 401, `Upstream` → 503, `AuthorRequired` → 403.
/// This preserves the SEC-001 AC-12 contract and the UI-002 failure-mode contract.
///
/// Optional params (DASH-001):
/// - `category=<text>` — exact match filter on the `category` column (bound param, not
///   interpolated — security.md A03).
/// - `sort=priority|date` — controls ORDER BY. Defaults to `priority` (backward-compatible).
///
/// # Errors
/// Extractor rejection if caller is not `author` or JWT invalid (AC-6, SEC-001 AC-12).
/// `ApiError::InvalidRequest` if `limit`/`offset` are out of range (AC-7).
/// `ApiError::UpstreamUnavailable` if DB is unreachable (Failure modes).
pub async fn list_tickets(
    author: RequireAuthor,
    Extension(req_id): Extension<RequestId>,
    Query(params): Query<TicketsQuery>,
    State(state): State<Arc<AppState>>,
) -> Result<Json<TicketsResponse>, ApiError> {
    let request_id = &req_id.0;

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

    // DASH-001: fetch rows using the appropriate literal SQL for each sort/filter
    // combination. Sort ordering is expressed as separate query literals — never
    // interpolated from user input (security.md A03). Category is always a bound $N param.
    let rows: Vec<TicketRow> = fetch_ticket_rows(
        pool,
        limit,
        offset,
        params.category.as_deref(),
        &params.sort,
    )
    .await
    .map_err(|_| ApiError::UpstreamUnavailable)?;

    let total = fetch_ticket_count(pool, params.category.as_deref())
        .await
        .map_err(|_| ApiError::UpstreamUnavailable)?;

    let latency_ms = u64::try_from(start.elapsed().as_millis()).unwrap_or(u64::MAX);
    let item_count = rows.len();

    // AC-23: structured log — fields request_id, user_id, tier, latency_ms, count.
    // Never logs `question` (PII, security.md §A09).
    tracing::info!(
        event = "dashboard.tickets.list",
        request_id = %request_id,
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
            judges_not_passed: r.judges_not_passed,
        })
        .collect();

    Ok(Json(TicketsResponse {
        items,
        total,
        limit,
        offset,
    }))
}

/// Fetch ticket rows applying optional category filter and sort order.
///
/// Four separate literal SQL strings carry the four combinations of
/// (category filter present/absent) × (sort by priority/date).
/// No `format!` or string interpolation is used — ORDER BY and WHERE clauses
/// are chosen in Rust match arms, not embedded from user input (security.md A03).
async fn fetch_ticket_rows(
    pool: &sqlx::PgPool,
    limit: i64,
    offset: i64,
    category: Option<&str>,
    sort: &SortOrder,
) -> Result<Vec<TicketRow>, sqlx::Error> {
    match (category, sort) {
        (None, SortOrder::Priority) => {
            sqlx::query_as(
                "SELECT id, conversation_id, question, category, priority_score, status, \
                 created_at, updated_at, judges_not_passed \
                 FROM tickets \
                 WHERE status = 'open' \
                 ORDER BY priority_score DESC, created_at DESC \
                 LIMIT $1 OFFSET $2",
            )
            .bind(limit)
            .bind(offset)
            .fetch_all(pool)
            .await
        }
        (None, SortOrder::Date) => {
            sqlx::query_as(
                "SELECT id, conversation_id, question, category, priority_score, status, \
                 created_at, updated_at, judges_not_passed \
                 FROM tickets \
                 WHERE status = 'open' \
                 ORDER BY created_at DESC, priority_score DESC \
                 LIMIT $1 OFFSET $2",
            )
            .bind(limit)
            .bind(offset)
            .fetch_all(pool)
            .await
        }
        (Some(cat), SortOrder::Priority) => {
            sqlx::query_as(
                "SELECT id, conversation_id, question, category, priority_score, status, \
                 created_at, updated_at, judges_not_passed \
                 FROM tickets \
                 WHERE status = 'open' AND category = $1 \
                 ORDER BY priority_score DESC, created_at DESC \
                 LIMIT $2 OFFSET $3",
            )
            .bind(cat)
            .bind(limit)
            .bind(offset)
            .fetch_all(pool)
            .await
        }
        (Some(cat), SortOrder::Date) => {
            sqlx::query_as(
                "SELECT id, conversation_id, question, category, priority_score, status, \
                 created_at, updated_at, judges_not_passed \
                 FROM tickets \
                 WHERE status = 'open' AND category = $1 \
                 ORDER BY created_at DESC, priority_score DESC \
                 LIMIT $2 OFFSET $3",
            )
            .bind(cat)
            .bind(limit)
            .bind(offset)
            .fetch_all(pool)
            .await
        }
    }
}

/// Fetch the total count of open tickets, optionally filtered by category.
async fn fetch_ticket_count(
    pool: &sqlx::PgPool,
    category: Option<&str>,
) -> Result<i64, sqlx::Error> {
    let row: CountRow = match category {
        None => {
            sqlx::query_as("SELECT count(*) as count FROM tickets WHERE status = 'open'")
                .fetch_one(pool)
                .await?
        }
        Some(cat) => {
            sqlx::query_as(
                "SELECT count(*) as count FROM tickets WHERE status = 'open' AND category = $1",
            )
            .bind(cat)
            .fetch_one(pool)
            .await?
        }
    };
    Ok(row.count)
}
