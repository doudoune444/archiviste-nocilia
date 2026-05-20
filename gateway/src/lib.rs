//! Archiviste gateway — public Axum HTTP layer.
//!
//! Exposes the public REST API and proxies internal calls to the workers tier.

pub mod auth;
pub mod config;
pub mod handlers;
pub mod routes;
pub mod state;

use anyhow::Result;
use axum::{
    extract::Request,
    http::{HeaderValue, StatusCode},
    middleware::{self, Next},
    response::{IntoResponse, Response},
    routing::get,
    Json, Router,
};
use serde::Serialize;
use std::{net::SocketAddr, sync::Arc};
use tower_http::{
    limit::RequestBodyLimitLayer,
    services::{ServeDir, ServeFile},
    set_header::SetResponseHeaderLayer,
    trace::TraceLayer,
};
use uuid::Uuid;

use crate::{
    auth::{
        extractor::{AnonIdentity, UserTier},
        fingerprint::{
            compute_fingerprint, extract_ip, extract_user_agent, fingerprint_to_user_id,
            parse_anon_cookie, ANON_COOKIE_NAME,
        },
        jwt,
    },
    config::Config,
    state::AppState,
};

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
// Anonymous identity middleware (AC-9, AC-10)
// ---------------------------------------------------------------------------

/// Middleware: resolve caller identity (authenticated member/author via JWT,
/// or anonymous via fingerprint).
///
/// Attaches `AnonIdentity` extension for all requests.  Also sets the
/// `archiviste_anon` cookie on the response if the caller is anonymous
/// and the cookie was absent from the request.
async fn resolve_identity(
    axum::extract::State(state): axum::extract::State<Arc<AppState>>,
    mut req: Request,
    next: Next,
) -> Response {
    let headers = req.headers().clone();

    // Attempt JWT authentication first.
    let maybe_auth = try_authenticate_jwt(&headers, &state).await;

    let anon_cookie_to_set = if maybe_auth.is_none() {
        // Anonymous path: read or generate archiviste_anon cookie.
        let existing = parse_anon_cookie(&headers);
        if existing.is_none() {
            Some(Uuid::new_v4().to_string())
        } else {
            None // cookie already exists, no need to set it
        }
    } else {
        None
    };

    let identity = if let Some(auth) = maybe_auth {
        // Authenticated member or author.
        AnonIdentity {
            user_id: auth.user_id,
            tier: auth.tier,
            fingerprint: None,
        }
    } else {
        // Anonymous: compute fingerprint from IP + UA + anon cookie.
        let anon_id = parse_anon_cookie(&headers)
            .or_else(|| anon_cookie_to_set.clone())
            .unwrap_or_else(|| Uuid::new_v4().to_string());

        // ConnectInfo is not available in `oneshot` tests (no TCP socket).
        let connect_info: Option<SocketAddr> = req
            .extensions()
            .get::<axum::extract::ConnectInfo<SocketAddr>>()
            .map(|ci| ci.0);

        let ip = extract_ip(&headers, connect_info.as_ref());
        let ua = extract_user_agent(&headers);
        let fp = compute_fingerprint(&ip, ua, &anon_id);
        let user_id = fingerprint_to_user_id(&fp);

        AnonIdentity {
            user_id,
            tier: UserTier::Anonymous,
            fingerprint: Some(fp),
        }
    };

    req.extensions_mut().insert(identity);

    let mut resp = next.run(req).await;

    // Set archiviste_anon cookie if this is a new anonymous visitor (AC-10).
    if let Some(new_cookie_val) = anon_cookie_to_set {
        let cookie_str = format!(
            "{ANON_COOKIE_NAME}={new_cookie_val}; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=31536000"
        );
        if let Ok(hv) = HeaderValue::from_str(&cookie_str) {
            resp.headers_mut().insert("set-cookie", hv);
        }
    }

    resp
}

/// Attempt to authenticate the request via JWT.
///
/// Returns `Some(auth)` if a valid, non-revoked JWT is present.
/// Returns `None` if no JWT is present or it is invalid (anonymous fallback).
async fn try_authenticate_jwt(
    headers: &axum::http::HeaderMap,
    state: &Arc<AppState>,
) -> Option<crate::auth::extractor::AuthUser> {
    use crate::auth::{extractor::AuthUser, sessions, sessions::SessionError};

    let token = extract_session_token(headers)?;
    let claims = jwt::verify(&token, &state.config.jwt_ed25519_public_key_pem).ok()?;

    let user_id = Uuid::parse_str(&claims.sub).ok()?;
    let sid = Uuid::parse_str(&claims.sid).ok()?;
    let tier = UserTier::from_claim(&claims.tier)?;

    // AC-13: check session in DB.
    let pool = state.db_pool.as_ref()?;
    match sessions::check_session(pool, sid).await {
        Ok(()) => {}
        Err(SessionError::Revoked | SessionError::Unavailable) => return None,
    }

    Some(AuthUser { user_id, tier, sid })
}

/// Extract session token from cookie (`archiviste_session`) or `Authorization` header.
///
/// Cookie takes priority over Authorization header (D-6).
fn extract_session_token(headers: &axum::http::HeaderMap) -> Option<String> {
    if let Some(cookie_header) = headers.get(axum::http::header::COOKIE) {
        if let Ok(s) = cookie_header.to_str() {
            for part in s.split(';') {
                let part = part.trim();
                if let Some(val) = part.strip_prefix("archiviste_session=") {
                    if !val.is_empty() {
                        return Some(val.to_string());
                    }
                }
            }
        }
    }

    let auth = headers
        .get(axum::http::header::AUTHORIZATION)?
        .to_str()
        .ok()?;
    auth.strip_prefix("Bearer ").map(str::to_string)
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
        .route("/v1/chat", axum::routing::post(handlers::chat::chat))
        .layer(RequestBodyLimitLayer::new(1_048_576)) // 1 MiB (AC-7)
        .layer(middleware::from_fn(handle_body_limit_error));

    // Static file routes: index.html at /, assets under /assets/*.
    // ServeDir handles path-traversal rejection natively (AC-5).
    let static_router = Router::new()
        .route_service("/", ServeFile::new("static/index.html"))
        .nest_service("/assets", ServeDir::new("static/assets"));

    // Public API routes (AC-11: marker #[public] — all handlers accept anonymous).
    let public_api = Router::new().route("/v1/me", get(routes::me::me));

    // Author-gated test route (AC-16 contract test).
    // Debug builds only — never compiled into release binaries. `security.md` §A05.
    #[cfg(debug_assertions)]
    let test_routes = Router::new().route("/v1/author-test", get(author_test_handler));
    #[cfg(not(debug_assertions))]
    let test_routes = Router::<Arc<AppState>>::new();

    // Root router: merge API + static, then apply global security headers (AC-6).
    Router::new()
        .route("/healthz", get(handlers::health::healthz))
        .merge(chat_router)
        .merge(public_api)
        .merge(test_routes)
        .merge(static_router)
        .layer(middleware::from_fn_with_state(
            Arc::clone(&state),
            resolve_identity,
        ))
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

// ---------------------------------------------------------------------------
// Test-only: author-gated handler (AC-16)
// ---------------------------------------------------------------------------

/// Handler only accessible to `author` tier.  Used by `auth_extractor_test.rs`
/// to exercise the `RequireAuthor` extractor (AC-16).
/// Only compiled in debug builds — not in release.
#[cfg(debug_assertions)]
async fn author_test_handler(
    auth: Result<crate::auth::extractor::RequireAuthor, crate::auth::extractor::AuthError>,
) -> Response {
    match auth {
        Ok(_) => (StatusCode::OK, "author ok").into_response(),
        Err(e) => e.into_response(),
    }
}

/// Bind to `config.bind_addr` and serve the gateway router until shutdown.
///
/// # Errors
///
/// Returns an error if `AppState` initialization fails, the TCP listener
/// cannot bind, or the Axum server exits with an error.
pub async fn run(config: Config) -> Result<()> {
    let addr = config.bind_addr.clone();

    // Production: connect to Postgres and verify the public key loads.
    let pool = sqlx::postgres::PgPoolOptions::new()
        .max_connections(5)
        .connect(&config.database_url)
        .await?;

    let state = Arc::new(AppState::new_with_pool(config, pool)?);
    let app = router(state);

    let listener = tokio::net::TcpListener::bind(&addr).await?;
    tracing::info!(%addr, "gateway listening");
    axum::serve(listener, app).await?;
    Ok(())
}
