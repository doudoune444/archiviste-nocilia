//! HTTP handlers grouped by feature area.
//!
//! Also exposes [`json_utf8`], a shared helper that wraps any `Serialize` body
//! into a `200 OK` response with `Content-Type: application/json; charset=utf-8`
//! (security.md §A05 — default content-type must include charset).

pub mod board;
pub mod chat;
pub mod conversations;
pub mod dashboard;
pub mod health;
pub mod quality;
pub mod report_contradiction;
pub mod stats;
pub mod status;
pub mod tickets;
pub mod workers_proxy;

use axum::{
    http::{HeaderValue, StatusCode},
    response::{IntoResponse, Response},
    Json,
};
use serde::Serialize;

/// Produce a `200 OK` JSON response with `Content-Type: application/json; charset=utf-8`.
///
/// Use this instead of bare `Json(body)` on every success path so that the
/// charset is included (security.md §A05).  The header array's `insert` call
/// replaces `Json`'s own `application/json` value, leaving exactly one
/// `content-type` header in the final response.
pub fn json_utf8<T: Serialize>(body: T) -> Response {
    (
        StatusCode::OK,
        [(
            axum::http::header::CONTENT_TYPE,
            HeaderValue::from_static("application/json; charset=utf-8"),
        )],
        Json(body),
    )
        .into_response()
}
