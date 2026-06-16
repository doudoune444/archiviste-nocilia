//! `GET /v1/board` — public read-only lore-gap board (BOARD-001).
//!
//! Returns open tickets for anonymous visitors. No auth required.
//! LIMIT enforced (security.md A01 — no bulk without LIMIT).
//! Uses `sqlx::query_as` (runtime-typed) to avoid offline-cache requirement
//! (same pattern as `handlers/tickets.rs`, blockers.md MED-3).

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

/// Query params for `GET /v1/board` (BOARD-001).
#[derive(Debug, Deserialize)]
pub struct BoardQuery {
    /// Max items returned. Range `[1, 100]`; default 50 (security.md A01 — LIMIT enforced).
    pub limit: Option<i64>,
    /// Offset for pagination. Must be `≥ 0`; default 0.
    pub offset: Option<i64>,
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
}

/// Handler: `GET /v1/board` — anonymous, paginated open tickets (BOARD-001).
///
/// Public (#[public] marker — no auth gate). Returns the same open-ticket
/// query as `GET /v1/tickets` but without the author-only gate.
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

    // Same SQL as tickets handler — only open tickets, ordered by priority DESC then date DESC.
    // Uses `sqlx::query_as` (runtime-typed) to avoid requiring a live DB at compile time.
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
        })
        .collect();

    Ok(Json(TicketsResponse {
        items,
        total: count_row.count,
        limit,
        offset,
    }))
}
