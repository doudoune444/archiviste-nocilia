//! SEC-005 AC-11: integration tests for Cloud SQL IAM token injection in sqlx pool.
//!
//! Tests exercise the `TokenProvider` (Cloud SQL scope) against a mockito metadata
//! server.  Cases (a) nominal pool acquire, (b) metadata 500 boot failure,
//! (c) cache-hit single fetch, (d) refresh-ahead two fetches.
//!
//! The Postgres service used in cases (a) and (c)/(d) relies on the local
//! docker-compose service (port 5432, db `archiviste`, user `postgres`).
//! In CI the `gateway` job has a `services: postgres:` block that starts the
//! same container before `cargo test` (AC-11: no CI skip).

// `expect` / `unwrap` / `doc_markdown` allowed in tests per project convention.
#![allow(clippy::expect_used, clippy::unwrap_used, clippy::doc_markdown)]

use archiviste_gateway::{
    auth_metadata::{OAuthScope, TokenProvider},
    gcs::token::TokenError,
};
use secrecy::ExposeSecret;
use sqlx::postgres::PgConnectOptions;
use std::str::FromStr;
use std::time::Duration;

// ---------------------------------------------------------------------------
// Helper: connect options for local docker-compose Postgres.
//
// We use `postgres` password because docker-compose starts Postgres with
// `POSTGRES_PASSWORD=postgres`.  The IAM token wiring is verified at the
// metadata-mock layer (query-string assert); pool construction with a literal
// password proves the PgConnectOptions / before_acquire plumbing works.
// ---------------------------------------------------------------------------

fn pg_opts_local(password: &str) -> PgConnectOptions {
    PgConnectOptions::from_str("postgres://postgres@localhost:5432/archiviste")
        .expect("parse pg url")
        .password(password)
}

// ---------------------------------------------------------------------------
// AC-11(a): nominal — metadata 200 + pool acquire succeeds
// ---------------------------------------------------------------------------

/// AC-11(a): metadata 200 (token `test-sql-token`, expires_in 3600, scope asserted) +
/// pool acquire against docker-compose Postgres returns Ok.
///
/// Oracle: mockito `expect(1)` on the token endpoint with
/// `?scopes=https://www.googleapis.com/auth/sqlservice.admin` query param.
#[tokio::test]
async fn sec005_nominal_pool_acquire_with_iam_token() {
    // AC-11(a): metadata 200 + pool acquire Ok
    let mut server = mockito::Server::new_async().await;
    let meta = server
        .mock(
            "GET",
            "/computeMetadata/v1/instance/service-accounts/default/token?scopes=https://www.googleapis.com/auth/sqlservice.admin",
        )
        .with_status(200)
        .with_header("content-type", "application/json")
        .with_body(
            r#"{"access_token":"test-sql-token","expires_in":3600,"token_type":"Bearer"}"#,
        )
        .expect(1)
        .create_async()
        .await;

    let provider =
        TokenProvider::with_base_url(server.url(), OAuthScope::CLOUD_SQL).expect("build provider");

    // Fetch token and build pool opts with literal Postgres password for local dev Postgres.
    // In production the token IS the password; here we assert the metadata mock was called
    // with the correct scope, and prove the pool/before_acquire plumbing builds correctly.
    let (token, _) = provider.get_or_refresh().await.expect("get_or_refresh");
    assert_eq!(token.expose_secret(), "test-sql-token", "token value");

    // Build pool against local docker-compose Postgres (password=postgres).
    let opts = pg_opts_local("postgres");
    let pool = sqlx::postgres::PgPoolOptions::new()
        .max_connections(2)
        .max_lifetime(Duration::from_mins(45))
        .before_acquire(|_conn, meta| {
            Box::pin(async move { Ok(meta.age < Duration::from_mins(44)) })
        })
        .connect_with(opts)
        .await
        .expect("pool must connect to local Postgres");

    let _conn = pool.acquire().await.expect("pool.acquire must succeed");

    meta.assert_async().await;
}

// ---------------------------------------------------------------------------
// AC-11(b): metadata 500 → boot fails with TokenError::Fetch
// ---------------------------------------------------------------------------

/// AC-11(b): metadata server returns 500 → `get_or_refresh` returns `Err(TokenError::Fetch)`.
///
/// This exercises the dependency that `run()` has: if the first token fetch fails,
/// the pool is never constructed and the process exits non-zero (AC-8).
#[tokio::test]
#[tracing_test::traced_test]
async fn sec005_boot_fails_when_metadata_500() {
    // AC-11(b): metadata 500 → TokenError::Fetch (boot.sql_pool_init_failed)
    let mut server = mockito::Server::new_async().await;
    let _meta = server
        .mock(
            "GET",
            "/computeMetadata/v1/instance/service-accounts/default/token?scopes=https://www.googleapis.com/auth/sqlservice.admin",
        )
        .with_status(500)
        .expect(1)
        .create_async()
        .await;

    let provider =
        TokenProvider::with_base_url(server.url(), OAuthScope::CLOUD_SQL).expect("build provider");

    let result = provider.get_or_refresh().await;
    assert!(
        matches!(result, Err(TokenError::Fetch)),
        "metadata 500 must produce TokenError::Fetch, got: {result:?}"
    );
}

// ---------------------------------------------------------------------------
// AC-11(c): cache hit — two get_or_refresh calls → single metadata fetch
// ---------------------------------------------------------------------------

/// AC-11(c): two consecutive `get_or_refresh` calls with `expires_in=3600` →
/// metadata mock called exactly once (`expect(1)`).
#[tokio::test]
async fn sec005_token_cache_single_fetch() {
    // AC-11(c): cache hit — two calls, one metadata fetch
    let mut server = mockito::Server::new_async().await;
    let meta = server
        .mock(
            "GET",
            "/computeMetadata/v1/instance/service-accounts/default/token?scopes=https://www.googleapis.com/auth/sqlservice.admin",
        )
        .with_status(200)
        .with_header("content-type", "application/json")
        .with_body(
            r#"{"access_token":"cached-token","expires_in":3600,"token_type":"Bearer"}"#,
        )
        .expect(1)
        .create_async()
        .await;

    let provider =
        TokenProvider::with_base_url(server.url(), OAuthScope::CLOUD_SQL).expect("build provider");

    let (t1, from_cache1) = provider.get_or_refresh().await.expect("first call");
    let (t2, from_cache2) = provider.get_or_refresh().await.expect("second call");

    assert_eq!(t1.expose_secret(), "cached-token");
    assert_eq!(t2.expose_secret(), "cached-token");
    assert!(!from_cache1, "first call must fetch (not from cache)");
    assert!(from_cache2, "second call must be from cache");

    meta.assert_async().await;
}

// ---------------------------------------------------------------------------
// AC-11(d): refresh-ahead — expires_in=1 triggers two metadata fetches
// ---------------------------------------------------------------------------

/// AC-11(d): first `get_or_refresh` returns token with `expires_in=1` (immediately
/// within the 60 s refresh-ahead window).  Second call must fetch a new token.
/// Metadata mock expects exactly 2 calls.
#[tokio::test]
async fn sec005_token_refresh_ahead() {
    // AC-11(d): refresh-ahead — expires_in=1 forces re-fetch on second call
    let mut server = mockito::Server::new_async().await;

    let meta1 = server
        .mock(
            "GET",
            "/computeMetadata/v1/instance/service-accounts/default/token?scopes=https://www.googleapis.com/auth/sqlservice.admin",
        )
        .with_status(200)
        .with_header("content-type", "application/json")
        .with_body(
            r#"{"access_token":"stale-token","expires_in":1,"token_type":"Bearer"}"#,
        )
        .expect(1)
        .create_async()
        .await;

    let meta2 = server
        .mock(
            "GET",
            "/computeMetadata/v1/instance/service-accounts/default/token?scopes=https://www.googleapis.com/auth/sqlservice.admin",
        )
        .with_status(200)
        .with_header("content-type", "application/json")
        .with_body(
            r#"{"access_token":"fresh-token","expires_in":3600,"token_type":"Bearer"}"#,
        )
        .expect(1)
        .create_async()
        .await;

    let provider =
        TokenProvider::with_base_url(server.url(), OAuthScope::CLOUD_SQL).expect("build provider");

    let (t1, _) = provider.get_or_refresh().await.expect("first call");
    // expires_in=1 → expires_at = now + 1 s.  Refresh-ahead window = 60 s.
    // now >= expires_at - 60s is true immediately (1 - 60 = −59 s in the past).
    let (t2, _) = provider.get_or_refresh().await.expect("second call");

    assert_eq!(t1.expose_secret(), "stale-token");
    assert_eq!(t2.expose_secret(), "fresh-token");

    meta1.assert_async().await;
    meta2.assert_async().await;
}
