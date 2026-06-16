//! Archiviste gateway — public Axum HTTP layer.
//!
//! Exposes the public REST API and proxies internal calls to the workers tier.

pub mod auth;
pub mod auth_metadata;
pub mod config;
pub mod errors;
pub mod gcs;
pub mod handlers;
pub mod health_cache;
pub mod middleware;
pub mod routes;
pub mod state;

use anyhow::{Context, Result};
use axum::{
    extract::Request,
    http::{HeaderValue, StatusCode},
    middleware::Next,
    response::{IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use secrecy::ExposeSecret;
use serde::Serialize;
use serde_json::json;
use sqlx::postgres::PgConnectOptions;
use std::{str::FromStr, sync::Arc, time::Duration};
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
        fingerprint::{cookie_uuid_to_user_id, parse_anon_cookie, ANON_COOKIE_NAME},
        jwt,
    },
    auth_metadata::TokenProvider,
    config::Config,
    state::AppState,
};

/// Maximum lifetime for a Cloud SQL connection before it is recycled.
///
/// Set to 45 min — safely below the Cloud SQL IAM token TTL of 60 min.
/// Combined with the `before_acquire` gate and the background refresher,
/// no physical connection ever outlives its token (SEC-005 AC-5).
///
/// `pub` so integration tests can import the constant and express the 44/45 min
/// boundary without magic literals (SEC-005 review LOW-7).
pub const SQL_CONNECTION_MAX_LIFETIME_SECS: u64 = 45 * 60;

/// Spawn a background task that refreshes the Cloud SQL IAM token every 30 min
/// and updates the pool's connect options so future physical connections use the
/// fresh token.
///
/// On refresh failure the task logs a warning and retries at the next tick.
/// The prior connect options remain in effect; combined with `max_lifetime = 45 min`
/// any connection established before a failed refresh will be recycled within 45 min,
/// well within the 60 min token TTL (SEC-005 failure mode).
fn spawn_sql_token_refresher(
    pool: sqlx::PgPool,
    token_provider: Arc<TokenProvider>,
    database_url: String,
) {
    tokio::spawn(async move {
        // MED-5: first tick at now + 30 min so the refresher does not run immediately
        // at boot (boot already holds a fresh token from the fail-fast fetch above).
        let start = tokio::time::Instant::now() + Duration::from_mins(30);
        let mut interval = tokio::time::interval_at(start, Duration::from_mins(30));
        loop {
            interval.tick().await;
            match token_provider.get_or_refresh().await {
                Ok((token, _)) => match PgConnectOptions::from_str(&database_url) {
                    Ok(opts) => {
                        pool.set_connect_options(opts.password(token.expose_secret()));
                    }
                    Err(e) => {
                        // MED-3: URL-parse failure is not an auth failure — map to `network`
                        // (config-shape failure of the connection target).
                        tracing::warn!(
                            event = "sql_pool.token_refresh_failed",
                            reason_code = "network",
                            error = %e,
                        );
                    }
                },
                Err(e) => {
                    // MED-3: use a distinct event for background refresh failures so the
                    // AC-14 runbook grep (`grep sql_pool.connection_failed`) stays clean.
                    tracing::warn!(
                        event = "sql_pool.token_refresh_failed",
                        reason_code = "metadata_token_failed",
                        error = %e,
                    );
                }
            }
        }
    });
}

/// Fetch the initial Cloud SQL IAM token and build the sqlx pool.
///
/// Extracted from `run()` so integration tests can drive the boot log path
/// without a full TCP listener (SEC-005 HIGH-1 / AC-8).
///
/// On token-fetch failure, logs `event="boot.sql_pool_init_failed"` with
/// `reason_code="metadata_token_failed"` and returns the `TokenError`.
///
/// On pool-connect failure, logs the same event with
/// `reason_code="cloud_sql_auth_failed"` and returns the sqlx error.
///
/// # Errors
///
/// Returns an error if the token fetch or pool construction fails.
pub async fn init_sql_pool(
    token_provider: &TokenProvider,
    database_url: &str,
) -> Result<sqlx::PgPool> {
    let (initial_token, _) = token_provider.get_or_refresh().await.map_err(|e| {
        tracing::error!(
            event = "boot.sql_pool_init_failed",
            reason_code = "metadata_token_failed",
            phase = "boot",
            error = %e,
        );
        anyhow::anyhow!("boot.sql_pool_init_failed reason_code=metadata_token_failed: {e}")
    })?;

    let opts = PgConnectOptions::from_str(database_url)
        .context("boot.sql_pool_init_failed reason_code=cloud_sql_auth_failed")?
        .password(initial_token.expose_secret());

    sqlx::postgres::PgPoolOptions::new()
        .max_connections(5)
        .max_lifetime(Duration::from_secs(SQL_CONNECTION_MAX_LIFETIME_SECS))
        .before_acquire(|_conn, meta| {
            Box::pin(async move {
                Ok::<bool, sqlx::Error>(
                    meta.age < Duration::from_secs(SQL_CONNECTION_MAX_LIFETIME_SECS - 60),
                )
            })
        })
        .connect_with(opts)
        .await
        .map_err(|e| {
            tracing::error!(
                event = "boot.sql_pool_init_failed",
                reason_code = "cloud_sql_auth_failed",
                phase = "boot",
                error = %e,
            );
            anyhow::anyhow!("boot.sql_pool_init_failed reason_code=cloud_sql_auth_failed: {e}")
        })
}

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
///
/// Fail-closed (HIGH-2 / security.md §A07): if the caller presented a
/// Bearer/session token AND the DB is unavailable, the middleware short-circuits
/// with 503 `upstream_unavailable` rather than silently downgrading to anonymous.
/// Only unauthenticated paths (no token) continue to the anonymous fingerprint branch.
async fn resolve_identity(
    axum::extract::State(state): axum::extract::State<Arc<AppState>>,
    mut req: Request,
    next: Next,
) -> Response {
    let headers = req.headers().clone();

    // Attempt JWT authentication — fail-closed if DB unreachable with a token present.
    let jwt_result = try_authenticate_jwt(&headers, &state).await;

    // Propagate 503 when JWT was present but session DB was unavailable (HIGH-2).
    let maybe_auth = match jwt_result {
        Err(JwtAuthError::DbUnavailable) => {
            return build_service_unavailable();
        }
        Ok(auth) => auth,
    };

    // IDN-001: identity is derived solely from the validated cookie UUID.
    // A missing or invalid cookie gets a fresh UUIDv4 (OsRng) — no fallback
    // to IP or User-Agent.
    let (anon_cookie_uuid, anon_cookie_to_set) = if maybe_auth.is_none() {
        if let Some(existing_uuid) = parse_anon_cookie(&headers) {
            (existing_uuid, None)
        } else {
            let fresh = Uuid::new_v4();
            (fresh, Some(fresh.to_string()))
        }
    } else {
        // Authenticated path — values are unused below.
        (Uuid::nil(), None)
    };

    let identity = if let Some(auth) = maybe_auth {
        // Authenticated member or author.
        AnonIdentity {
            user_id: auth.user_id,
            tier: auth.tier,
            fingerprint: None,
        }
    } else {
        // Anonymous: derive user_id from the validated cookie UUID alone.
        // IDN-001: `user_id = UUIDv5(NIL_namespace, cookie_uuid.as_bytes())`.
        // The cookie UUID string is stored in `fingerprint` for observability
        // (AC-9: anonymous callers expose a stable non-null fingerprint field).
        let user_id = cookie_uuid_to_user_id(&anon_cookie_uuid);

        AnonIdentity {
            user_id,
            tier: UserTier::Anonymous,
            fingerprint: Some(anon_cookie_uuid.to_string()),
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
            // Append (not insert) to preserve coexistence with other Set-Cookie headers
            // that may be added by future auth flows (e.g. PR-b login sets archiviste_session).
            resp.headers_mut().append("set-cookie", hv);
        }
    }

    resp
}

/// Internal error discriminator for `try_authenticate_jwt`.
///
/// Separates "no token / invalid token → go anonymous" from
/// "token present but DB down → fail-closed 503" (HIGH-2).
enum JwtAuthError {
    /// The session DB was unavailable while a structurally valid token was present.
    ///
    /// Caller MUST short-circuit with 503 — do not fall through to anonymous (security.md §A07).
    DbUnavailable,
}

/// Attempt to authenticate the request via JWT.
///
/// Returns `Ok(Some(auth))` if a valid, non-revoked JWT is present.
/// Returns `Ok(None)` if no JWT is present or it is invalid (anonymous fallback).
/// Returns `Err(JwtAuthError::DbUnavailable)` if a structurally valid JWT was
/// present but the session DB was unreachable — caller must respond 503 (HIGH-2).
async fn try_authenticate_jwt(
    headers: &axum::http::HeaderMap,
    state: &Arc<AppState>,
) -> Result<Option<crate::auth::extractor::AuthUser>, JwtAuthError> {
    use crate::auth::{extractor::AuthUser, sessions, sessions::SessionError};

    // If no token is present, caller is anonymous — not an error.
    let Some(token) = extract_session_token(headers) else {
        return Ok(None);
    };

    // Invalid JWT (bad sig, wrong alg, expired…) → anonymous, not an error.
    let Ok(claims) = jwt::verify(&token, &state.config.jwt_ed25519_public_key_pem) else {
        return Ok(None);
    };

    let Ok(user_id) = Uuid::parse_str(&claims.sub) else {
        return Ok(None);
    };
    let Ok(sid) = Uuid::parse_str(&claims.sid) else {
        return Ok(None);
    };
    let Some(tier) = UserTier::from_claim(&claims.tier) else {
        return Ok(None);
    };

    // AC-13: server-side session check.
    // If no pool is configured (test env), skip the check — session is assumed valid.
    // In production, absence of a pool is caught at boot via `run()` → panic.
    let Some(pool) = state.db_pool.as_ref() else {
        // Test path: no DB available, treat as valid session (AC-13 tested in PR-b).
        return Ok(Some(AuthUser { user_id, tier, sid }));
    };

    match sessions::check_session(pool, sid).await {
        Ok(()) => {}
        Err(SessionError::Revoked) => return Ok(None),
        // Token was present and structurally valid, but DB is down → fail-closed 503 (HIGH-2).
        Err(SessionError::Unavailable) => return Err(JwtAuthError::DbUnavailable),
    }

    Ok(Some(AuthUser { user_id, tier, sid }))
}

/// Build a 503 `upstream_unavailable` response for the fail-closed DB path (HIGH-2).
fn build_service_unavailable() -> Response {
    (
        StatusCode::SERVICE_UNAVAILABLE,
        [(
            axum::http::header::CONTENT_TYPE,
            HeaderValue::from_static("application/json; charset=utf-8"),
        )],
        Json(json!({
            "error": "upstream_unavailable",
        })),
    )
        .into_response()
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
// Auth sub-router: body-limit 4 KiB + Content-Type enforcement (AC-17)
// ---------------------------------------------------------------------------

/// Middleware: reject requests with `Content-Type` ≠ `application/json` (AC-17).
///
/// Applied only to the auth sub-router.
async fn require_json_content_type(req: Request, next: Next) -> Response {
    let is_json = req
        .headers()
        .get(axum::http::header::CONTENT_TYPE)
        .and_then(|v| v.to_str().ok())
        .is_some_and(|v| v.starts_with("application/json"));

    if !is_json {
        return (
            StatusCode::UNSUPPORTED_MEDIA_TYPE,
            [(
                axum::http::header::CONTENT_TYPE,
                HeaderValue::from_static("application/json; charset=utf-8"),
            )],
            Json(json!({
                "error": "unsupported_media_type",
            })),
        )
            .into_response();
    }

    next.run(req).await
}

/// Convert body-limit 413 into the proper JSON error for auth routes (AC-17).
async fn handle_auth_body_limit_error(req: Request, next: Next) -> Response {
    let request_id = req
        .extensions()
        .get::<RequestId>()
        .map_or_else(|| Uuid::new_v4().to_string(), |r| r.0.clone());

    let resp = next.run(req).await;

    if resp.status() == StatusCode::PAYLOAD_TOO_LARGE {
        return (
            StatusCode::PAYLOAD_TOO_LARGE,
            [(
                axum::http::header::CONTENT_TYPE,
                HeaderValue::from_static("application/json; charset=utf-8"),
            )],
            Json(json!({
                "error": "payload_too_large",
                "request_id": request_id,
            })),
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
    // `/v1/chat` sub-router with body limit + error-handler + overhead timing middleware.
    let chat_router = Router::new()
        .route("/v1/chat", axum::routing::post(handlers::chat::chat))
        .layer(RequestBodyLimitLayer::new(1_048_576)) // 1 MiB (AC-7)
        .layer(axum::middleware::from_fn(handle_body_limit_error))
        // OPS-001a: inserts `X-Gateway-Overhead-Ms` on every response (AC-4).
        .layer(axum::middleware::from_fn(
            crate::middleware::overhead_header,
        ));

    // Static file routes: index.html at /, observability page, assets under /assets/*.
    // ServeDir handles path-traversal rejection natively (AC-5).
    // OBS-001: /observability mounted here — public, no auth (AC-2).
    let static_router = Router::new()
        .route_service("/", ServeFile::new("static/index.html"))
        .route_service(
            "/observability",
            ServeFile::new("static/observability.html"),
        )
        .nest_service("/assets", ServeDir::new("static/assets"));

    // Auth sub-router: body limit 4 KiB + Content-Type: application/json enforcement (AC-17).
    let auth_router = Router::new()
        .route("/v1/auth/signup", post(routes::auth::signup))
        .route("/v1/auth/login", post(routes::auth::login))
        .route("/v1/auth/logout", post(routes::auth::logout))
        // AC-17: 4 KiB body limit, stricter than the general 1 MiB chat limit.
        .layer(RequestBodyLimitLayer::new(4_096))
        .layer(axum::middleware::from_fn(handle_auth_body_limit_error))
        // AC-17: require Content-Type: application/json.
        .layer(axum::middleware::from_fn(require_json_content_type));

    // Public API routes (AC-11: marker #[public] — all handlers accept anonymous).
    // OBS-001: /v1/stats mounted here — no auth gate (AC-6).
    // OBS-002: /v1/status mounted here — anonymous, no auth guard (AC-7).
    // OBS-004: /v1/quality mounted here — anonymous, no auth guard (AC-5).
    let public_api = Router::new()
        .route("/v1/me", get(routes::me::me))
        .route("/v1/stats", get(handlers::stats::stats))
        .route("/v1/status", get(handlers::status::status))
        .route("/v1/quality", get(handlers::quality::quality));

    // Author-gated dashboard API routes (UI-002 PR1 — AC-5..AC-12, AC-19..AC-23).
    // `RequireAuthor` extractor gates each handler — no `#[public]` marker (AC-11).
    // UI-002b PR2: GET /dashboard (AC-1, AC-11) added — custom handler so RequireAuthor
    // extractor can gate it (a bare ServeFile carries no extractor, plan R3).
    let dashboard_api = Router::new()
        .route("/dashboard", get(handlers::dashboard::serve_dashboard))
        .route("/v1/tickets", get(handlers::tickets::list_tickets))
        .route(
            "/v1/conversations/{id}/signed-url",
            get(handlers::conversations::signed_url),
        );

    // Author-gated test route (AC-16 contract test).
    // Debug builds only — never compiled into release binaries. `security.md` §A05.
    #[cfg(debug_assertions)]
    let test_routes = Router::new().route("/v1/author-test", get(author_test_handler));
    #[cfg(not(debug_assertions))]
    let test_routes = Router::<Arc<AppState>>::new();

    // Root router: merge API + static, then apply global security headers (AC-6).
    Router::new()
        .route("/healthz", get(handlers::health::healthz))
        // `/health` aliases `/healthz`: Cloud Run's public frontend reserves the
        // literal `/healthz` path and 404s it before it reaches the container, so
        // the Deploy smoke test probes `/health`. Same handler for both.
        .route("/health", get(handlers::health::healthz))
        .merge(chat_router)
        .merge(auth_router)
        .merge(public_api)
        .merge(dashboard_api)
        .merge(test_routes)
        .merge(static_router)
        .layer(axum::middleware::from_fn_with_state(
            Arc::clone(&state),
            resolve_identity,
        ))
        .layer(axum::middleware::from_fn(attach_request_id))
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

    // SEC-005 AC-8: build provider (HTTP client only — no I/O yet).
    let sql_token_provider = Arc::new(
        TokenProvider::for_cloud_sql()
            .context("boot.sql_pool_init_failed reason_code=metadata_token_failed")?,
    );

    // SEC-005 AC-8: fetch initial token + build pool.  Fail-fast if metadata unreachable.
    // init_sql_pool emits `boot.sql_pool_init_failed` with the appropriate reason_code.
    let pool = init_sql_pool(&sql_token_provider, &config.database_url).await?;

    // SEC-005 AC-4: background refresher updates pool connect options every 30 min.
    spawn_sql_token_refresher(
        pool.clone(),
        Arc::clone(&sql_token_provider),
        config.database_url.clone(),
    );

    let state = Arc::new(AppState::new_with_pool_and_sql_token_provider(
        config,
        pool,
        sql_token_provider,
    )?);
    let app = router(state);

    let listener = tokio::net::TcpListener::bind(&addr).await?;
    tracing::info!(%addr, "gateway listening");
    axum::serve(listener, app).await?;
    Ok(())
}
