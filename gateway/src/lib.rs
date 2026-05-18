//! Archiviste gateway — public Axum HTTP layer.
//!
//! Exposes the public REST API and proxies internal calls to the workers tier.

pub mod config;
pub mod handlers;
pub mod state;

use anyhow::Result;
use axum::{
    extract::Request,
    http::{HeaderValue, StatusCode},
    middleware::{self, Next},
    response::{IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use serde::Serialize;
use std::sync::Arc;
use tower_http::{
    limit::RequestBodyLimitLayer,
    services::{ServeDir, ServeFile},
    set_header::SetResponseHeaderLayer,
    trace::TraceLayer,
};
use uuid::Uuid;

use crate::{config::Config, state::AppState};

// ---------------------------------------------------------------------------
// Request-id middleware (R2)
// ---------------------------------------------------------------------------

/// Extension type carrying the gateway-generated request identifier.
///
/// Generated upstream of `RequestBodyLimitLayer` so that the error handler
/// for oversized bodies can include the `request_id` in the 400 envelope.
#[derive(Clone)]
pub struct RequestId(pub String);

/// Middleware: generate a `UUIDv4` `request_id` and attach it to request extensions.
///
/// AC-16: any client-supplied `X-Request-Id` header is not forwarded — only
/// our generated value propagates.
async fn attach_request_id(mut req: Request, next: Next) -> Response {
    let id = Uuid::new_v4().to_string();
    req.extensions_mut().insert(RequestId(id));
    next.run(req).await
}

// ---------------------------------------------------------------------------
// Body-limit error handler
// ---------------------------------------------------------------------------

/// Error shape used by the body-limit rejection handler.
#[derive(Serialize)]
struct BodyLimitError {
    error: &'static str,
    request_id: String,
}

/// Convert a `RequestBodyLimitLayer` rejection (413) into the uniform 400 envelope.
///
/// The `request_id` is read from request extensions (set by `attach_request_id`).
async fn handle_body_limit_error(req: Request, next: Next) -> Response {
    let request_id = req
        .extensions()
        .get::<RequestId>()
        .map_or_else(|| Uuid::new_v4().to_string(), |r| r.0.clone());

    let resp = next.run(req).await;

    if resp.status() == StatusCode::PAYLOAD_TOO_LARGE {
        // Rewrite 413 → 400 with uniform envelope (AC-7).
        let body = BodyLimitError {
            error: "invalid_request",
            request_id,
        };
        return (
            StatusCode::BAD_REQUEST,
            [(
                axum::http::header::CONTENT_TYPE,
                HeaderValue::from_static("application/json; charset=utf-8"),
            )],
            Json(body),
        )
            .into_response();
    }

    resp
}

// ---------------------------------------------------------------------------
// Router
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Security header constants (AC-6 literal values)
// ---------------------------------------------------------------------------

const CSP: &str = "default-src 'self'; script-src 'self'; style-src 'self'; \
    img-src 'self' data:; object-src 'none'; frame-ancestors 'none'; \
    base-uri 'none'; form-action 'self'";

/// HSTS value: 1-year max-age, includeSubDomains, preload eligibility (SEC-003 AC-1 / A02).
const HSTS_VALUE: &str = "max-age=31536000; includeSubDomains; preload";

/// Build the gateway router with all routes wired and shared state attached.
pub fn router(state: Arc<AppState>) -> Router {
    // `/v1/chat` sub-router with body limit + error-handler middleware.
    let chat_router = Router::new()
        .route("/v1/chat", post(handlers::chat::chat))
        .layer(RequestBodyLimitLayer::new(1_048_576)) // 1 MiB (AC-7)
        .layer(middleware::from_fn(handle_body_limit_error));

    // Static file routes: index.html at /, assets under /assets/*.
    // ServeDir handles path-traversal rejection natively (AC-5).
    let static_router = Router::new()
        .route_service("/", ServeFile::new("static/index.html"))
        .nest_service("/assets", ServeDir::new("static/assets"));

    // Root router: merge API + static, then apply global security headers (AC-6).
    Router::new()
        .route("/healthz", get(handlers::health::healthz))
        .merge(chat_router)
        .merge(static_router)
        .layer(middleware::from_fn(attach_request_id))
        .with_state(state)
        // SEC-003 / AC-1 / A02: 5 security headers applied router-wide (static + API).
        .layer(SetResponseHeaderLayer::if_not_present(
            axum::http::header::HeaderName::from_static("strict-transport-security"),
            HeaderValue::from_static(HSTS_VALUE),
        ))
        .layer(SetResponseHeaderLayer::if_not_present(
            axum::http::header::HeaderName::from_static("x-frame-options"),
            HeaderValue::from_static("DENY"),
        ))
        .layer(SetResponseHeaderLayer::if_not_present(
            axum::http::header::HeaderName::from_static("referrer-policy"),
            HeaderValue::from_static("strict-origin-when-cross-origin"),
        ))
        .layer(SetResponseHeaderLayer::if_not_present(
            axum::http::header::HeaderName::from_static("x-content-type-options"),
            HeaderValue::from_static("nosniff"),
        ))
        .layer(SetResponseHeaderLayer::if_not_present(
            axum::http::header::HeaderName::from_static("content-security-policy"),
            HeaderValue::from_static(CSP),
        ))
        .layer(TraceLayer::new_for_http())
}

/// Bind to `config.bind_addr` and serve the gateway router until shutdown.
///
/// # Errors
///
/// Returns an error if `AppState` initialization fails, the TCP listener
/// cannot bind, or the Axum server exits with an error.
pub async fn run(config: Config) -> Result<()> {
    let addr = config.bind_addr.clone();
    let state = Arc::new(AppState::new(config)?);
    let app = router(state);

    let listener = tokio::net::TcpListener::bind(&addr).await?;
    tracing::info!(%addr, "gateway listening");
    axum::serve(listener, app).await?;
    Ok(())
}
