//! Uniform API error envelope for dashboard endpoints.
//!
//! All handler `Err(ApiError)` variants produce `{"error":"<code>","request_id":"<uuid>"}`.
//! Codes are byte-for-byte identical to SEC-001 error codes (AC-2, AC-6, AC-10).

use axum::{
    http::{HeaderValue, StatusCode},
    response::{IntoResponse, Response},
    Json,
};
use serde::Serialize;
use uuid::Uuid;

/// Uniform API error for dashboard routes (AC-2, AC-6, AC-7, AC-10, Failure modes).
#[derive(Debug)]
pub enum ApiError {
    /// `limit`/`offset` out of range or non-integer (AC-7). → 400.
    InvalidRequest,
    /// Caller is not `author` tier (AC-6, AC-10 sub-case c). → 403.
    AuthorRequired,
    /// Conversation UUID not found in DB (AC-10 sub-case a). → 404.
    ConversationNotFound,
    /// DB or signing backend unreachable (Failure modes). → 503.
    UpstreamUnavailable,
}

/// Wire format of error responses (`{"error":"<code>","request_id":"<uuid>"}`).
#[derive(Serialize)]
struct ErrorBody {
    error: &'static str,
    request_id: String,
}

impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        let request_id = Uuid::new_v4().to_string();

        let (status, code) = match self {
            Self::InvalidRequest => (StatusCode::BAD_REQUEST, "invalid_request"),
            Self::AuthorRequired => (StatusCode::FORBIDDEN, "author_required"),
            Self::ConversationNotFound => (StatusCode::NOT_FOUND, "conversation_not_found"),
            Self::UpstreamUnavailable => (StatusCode::SERVICE_UNAVAILABLE, "upstream_unavailable"),
        };

        (
            status,
            [(
                axum::http::header::CONTENT_TYPE,
                HeaderValue::from_static("application/json; charset=utf-8"),
            )],
            Json(ErrorBody {
                error: code,
                request_id,
            }),
        )
            .into_response()
    }
}
