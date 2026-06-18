//! `GET /v1/board` — public read-only lore-gap board (BOARD-001).
//!
//! Returns open tickets for anonymous visitors. No auth required.
//! LIMIT enforced (security.md A01 — no bulk without LIMIT).
//! Uses `sqlx::query_as` (runtime-typed) to avoid offline-cache requirement
//! (same pattern as `handlers/tickets.rs`, blockers.md MED-3).
//!
//! BOARD-001 (AFK): optional `category` filter and `sort=priority|date` added.
//! Sort is represented as a Rust enum — never interpolated into SQL strings.
//! Two separate `sqlx::query_as` literal calls carry the fixed ORDER BY clause;
//! no `format!` is ever used (security.md A03 — injection prevention).

use std::sync::Arc;

use axum::response::Html;
use axum::{
    extract::{Query, State},
    Json,
};
use serde::Deserialize;

use crate::{
    errors::ApiError,
    handlers::tickets::{TicketItem, TicketsResponse},
    state::AppState,
};

/// The board HTML page embedded at compile time (BOARD-001).
///
/// Served by `serve_board` so there is a single binary artifact (same pattern as dashboard).
const BOARD_HTML: &str = include_str!("../../static/board.html");

/// Sort order for `GET /v1/board` (BOARD-001 AFK).
///
/// Deserialised from the `sort` query param. Defaults to `Priority`.
/// Illegal values are rejected by serde and fall through to `Default` via
/// `#[serde(default)]` on the enclosing struct field — unknown strings yield
/// `SortOrder::default()` (priority ordering), never a 400.
#[derive(Debug, Deserialize, Default, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum SortOrder {
    /// Order by `priority_score DESC, created_at DESC` (default, backward-compatible).
    #[default]
    Priority,
    /// Order by `created_at DESC, priority_score DESC`.
    Date,
}

/// Query params for `GET /v1/board` (BOARD-001).
#[derive(Debug, Deserialize)]
pub struct BoardQuery {
    /// Max items returned. Range `[1, 100]`; default 50 (security.md A01 — LIMIT enforced).
    pub limit: Option<i64>,
    /// Offset for pagination. Must be `≥ 0`; default 0.
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

/// Handler: `GET /board` — serves the public board HTML page (BOARD-001).
///
/// Public route — no auth extractor. The global security-header middleware
/// (SEC-003) covers this route automatically.
pub async fn serve_board() -> Html<&'static str> {
    Html(BOARD_HTML)
}

/// Count row for `SELECT count(*)`.
#[derive(Debug, sqlx::FromRow)]
struct CountRow {
    count: i64,
}

/// Row shape returned by the open-tickets query.
#[derive(Debug, sqlx::FromRow)]
struct TicketRow {
    id: uuid::Uuid,
    conversation_id: uuid::Uuid,
    question: String,
    category: String,
    priority_score: i32,
    status: String,
    created_at: chrono::DateTime<chrono::Utc>,
    updated_at: chrono::DateTime<chrono::Utc>,
    /// True when the ticket was raised via human "send anyway" — judges did not confirm (#163).
    judges_not_passed: bool,
}

/// Handler: `GET /v1/board` — anonymous, paginated open tickets (BOARD-001).
///
/// Public (#[public] marker — no auth gate). Returns the same open-ticket
/// query as `GET /v1/tickets` but without the author-only gate.
///
/// Optional params (BOARD-001 AFK):
/// - `category=<text>` — exact match filter on the `category` column (bound param, not
///   interpolated — security.md A03).
/// - `sort=priority|date` — controls ORDER BY. Defaults to `priority` (backward-compatible).
///
/// # Errors
/// `ApiError::InvalidRequest` if `limit`/`offset` are out of range.
/// `ApiError::UpstreamUnavailable` if DB is unreachable.
pub async fn list_board(
    Query(params): Query<BoardQuery>,
    State(state): State<Arc<AppState>>,
) -> Result<Json<TicketsResponse>, ApiError> {
    // BOARD-001: LIMIT enforced — range [1, 100] (security.md A01 — no bulk without LIMIT).
    // Cap at 100 for the public board (tighter than the author dashboard's 200).
    let limit = params.limit.unwrap_or(50);
    let offset = params.offset.unwrap_or(0);
    if !(1..=100).contains(&limit) || offset < 0 {
        return Err(ApiError::InvalidRequest);
    }

    let pool = state
        .db_pool
        .as_ref()
        .ok_or(ApiError::UpstreamUnavailable)?;

    // BOARD-001 AFK: fetch rows using the appropriate literal SQL for each sort/filter
    // combination. Sort ordering is expressed as separate query literals — never
    // interpolated from user input (security.md A03). Category is always a bound $N param.
    let rows: Vec<TicketRow> = fetch_board_rows(
        pool,
        limit,
        offset,
        params.category.as_deref(),
        &params.sort,
    )
    .await
    .map_err(|_| ApiError::UpstreamUnavailable)?;

    let total = fetch_board_count(pool, params.category.as_deref())
        .await
        .map_err(|_| ApiError::UpstreamUnavailable)?;

    // A09: never log question text (visitor-supplied content).
    tracing::info!(
        event = "board.tickets.list",
        count = rows.len(),
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
async fn fetch_board_rows(
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
async fn fetch_board_count(
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
