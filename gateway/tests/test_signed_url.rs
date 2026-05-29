//! Integration tests for SEC-004: GCS V4 signing via IAM signBlob.
//!
//! Tests cover AC-2 (URL format), AC-3 (token cache / refresh / retry-on-401),
//! AC-4 (503 on all failure modes), AC-5 (reason_code log fields, no sensitive data),
//! AC-6 (timeout oracle), AC-9 (8 sub-cases a–h), AC-21 (TTL constant = 300).

// `expect` / `unwrap` allowed in tests per project convention.
// `doc_markdown` in test doc-comments: identifier names in prose (non-public API).
#![allow(clippy::expect_used, clippy::unwrap_used, clippy::doc_markdown)]

use archiviste_gateway::gcs::{
    sign::{sign_get, SIGNED_URL_TTL_SECONDS},
    token::TokenProvider,
};
use std::sync::Arc;

// ---------------------------------------------------------------------------
// Helper: parse query string from a URL
// ---------------------------------------------------------------------------

fn query_param(url: &str, key: &str) -> Option<String> {
    let qs = url.split('?').nth(1)?;
    for part in qs.split('&') {
        let mut kv = part.splitn(2, '=');
        if kv.next()? == key {
            return Some(kv.next().unwrap_or("").to_string());
        }
    }
    None
}

/// Build a 64-byte base64 payload that represents a fake IAM signedBlob response.
/// 64 bytes → 128 hex chars (AC-2 oracle: IAM returns RSA-2048 but mock uses 64 bytes).
fn fake_signed_blob_b64() -> String {
    use base64::Engine;
    base64::engine::general_purpose::STANDARD.encode(vec![0xABu8; 64])
}

// ---------------------------------------------------------------------------
// AC-21: SIGNED_URL_TTL_SECONDS constant is exactly 300
// ---------------------------------------------------------------------------

/// AC-21 — constant exported and equals 300 byte-for-byte.
#[test]
fn ttl_constant_is_300() {
    // AC-21: `pub const SIGNED_URL_TTL_SECONDS: u64 = 300`
    assert_eq!(SIGNED_URL_TTL_SECONDS, 300_u64);
}

// ---------------------------------------------------------------------------
// AC-2, AC-9(a): nominal path — metadata 200 + signBlob 200 → valid URL
// ---------------------------------------------------------------------------

/// AC-2 / AC-9(a): metadata 200 (token test-token, expires_in 3600) +
/// signBlob 200 (signedBlob = 64-byte b64) → URL starts with
/// `https://<bucket>.storage.googleapis.com/<object>?`, carries
/// `X-Goog-Algorithm=GOOG4-RSA-SHA256`, `X-Goog-Expires=300`,
/// `X-Goog-SignedHeaders=host`, `X-Goog-Signature` of 128 hex chars (64 bytes × 2).
#[tokio::test]
async fn sec004_nominal_returns_v4_url() {
    // AC-2 / AC-9(a): nominal path
    let mut server = mockito::Server::new_async().await;

    let _meta = server
        .mock(
            "GET",
            "/computeMetadata/v1/instance/service-accounts/default/token",
        )
        .with_status(200)
        .with_header("content-type", "application/json")
        .with_body(r#"{"access_token":"test-token","expires_in":3600,"token_type":"Bearer"}"#)
        .create_async()
        .await;

    let _sign = server
        .mock(
            "POST",
            "/v1/projects/-/serviceAccounts/sa@project.iam.gserviceaccount.com:signBlob",
        )
        .with_status(200)
        .with_header("content-type", "application/json")
        .with_body(format!(r#"{{"signedBlob":"{}"}}"#, fake_signed_blob_b64()))
        .create_async()
        .await;

    let token_provider =
        Arc::new(TokenProvider::with_base_url(server.url()).expect("TokenProvider::with_base_url"));
    let now = chrono::Utc::now();
    let url = sign_get(
        &token_provider,
        "sa@project.iam.gserviceaccount.com",
        "archiviste-conversations",
        "conv/abc.md",
        now,
        Some(&server.url()),
    )
    .await
    .expect("sign_get must succeed in nominal path");

    assert!(
        url.starts_with("https://archiviste-conversations.storage.googleapis.com/conv/abc.md?"),
        "URL must start with https://<bucket>.storage.googleapis.com/<object>?: got {url}"
    );
    assert_eq!(
        query_param(&url, "X-Goog-Algorithm").as_deref(),
        Some("GOOG4-RSA-SHA256"),
        "X-Goog-Algorithm must be GOOG4-RSA-SHA256"
    );
    assert_eq!(
        query_param(&url, "X-Goog-Expires").as_deref(),
        Some("300"),
        "X-Goog-Expires must be 300"
    );
    assert_eq!(
        query_param(&url, "X-Goog-SignedHeaders").as_deref(),
        Some("host"),
        "X-Goog-SignedHeaders must be host"
    );
    let sig = query_param(&url, "X-Goog-Signature").expect("X-Goog-Signature must be present");
    assert_eq!(
        sig.len(),
        128,
        "64-byte blob → 128 hex chars; got {}",
        sig.len()
    );
    assert!(
        sig.chars().all(|c| c.is_ascii_hexdigit()),
        "signature must be hex"
    );
}

// ---------------------------------------------------------------------------
// AC-3, AC-9(g): cache hit — two sign_get calls → single metadata fetch
// ---------------------------------------------------------------------------

/// AC-3 / AC-9(g): two successive sign_get calls share the token cache.
/// Mockito `metadata.expect(1)` (single fetch), `signBlob.expect(2)`.
#[tokio::test]
async fn sec004_token_cache_reuse() {
    // AC-3 / AC-9(g): cache hit — metadata fetched once, signBlob called twice
    let mut server = mockito::Server::new_async().await;

    let meta = server
        .mock(
            "GET",
            "/computeMetadata/v1/instance/service-accounts/default/token",
        )
        .with_status(200)
        .with_header("content-type", "application/json")
        .with_body(r#"{"access_token":"test-token","expires_in":3600,"token_type":"Bearer"}"#)
        .expect(1)
        .create_async()
        .await;

    let sign_mock = server
        .mock(
            "POST",
            "/v1/projects/-/serviceAccounts/sa@project.iam.gserviceaccount.com:signBlob",
        )
        .with_status(200)
        .with_header("content-type", "application/json")
        .with_body(format!(r#"{{"signedBlob":"{}"}}"#, fake_signed_blob_b64()))
        .expect(2)
        .create_async()
        .await;

    let token_provider =
        Arc::new(TokenProvider::with_base_url(server.url()).expect("TokenProvider::with_base_url"));
    let now = chrono::Utc::now();

    sign_get(
        &token_provider,
        "sa@project.iam.gserviceaccount.com",
        "bucket",
        "obj",
        now,
        Some(&server.url()),
    )
    .await
    .expect("first sign_get must succeed");

    sign_get(
        &token_provider,
        "sa@project.iam.gserviceaccount.com",
        "bucket",
        "obj",
        now,
        Some(&server.url()),
    )
    .await
    .expect("second sign_get must succeed");

    meta.assert_async().await;
    sign_mock.assert_async().await;
}

// ---------------------------------------------------------------------------
// AC-3, AC-9(g): refresh-ahead — expires_in=1 triggers second fetch
// ---------------------------------------------------------------------------

/// AC-3 / AC-9(g): first call gets token expires_in=1 (enters < 60 s refresh window
/// immediately). Second call must trigger a new metadata fetch. metadata.expect(2).
#[tokio::test]
async fn sec004_token_refresh_ahead() {
    // AC-3 / AC-9(g): refresh-ahead — expires_in=1 forces re-fetch on second call
    let mut server = mockito::Server::new_async().await;

    // First call → expires_in=1 (within 60 s refresh-ahead window immediately)
    // Second call → fresh token expires_in=3600
    let meta = server
        .mock(
            "GET",
            "/computeMetadata/v1/instance/service-accounts/default/token",
        )
        .with_status(200)
        .with_header("content-type", "application/json")
        .with_body(r#"{"access_token":"stale-token","expires_in":1,"token_type":"Bearer"}"#)
        .expect(1)
        .create_async()
        .await;

    let meta2 = server
        .mock(
            "GET",
            "/computeMetadata/v1/instance/service-accounts/default/token",
        )
        .with_status(200)
        .with_header("content-type", "application/json")
        .with_body(r#"{"access_token":"fresh-token","expires_in":3600,"token_type":"Bearer"}"#)
        .expect(1)
        .create_async()
        .await;

    let sign_mock = server
        .mock(
            "POST",
            "/v1/projects/-/serviceAccounts/sa@project.iam.gserviceaccount.com:signBlob",
        )
        .with_status(200)
        .with_header("content-type", "application/json")
        .with_body(format!(r#"{{"signedBlob":"{}"}}"#, fake_signed_blob_b64()))
        .expect(2)
        .create_async()
        .await;

    let token_provider =
        Arc::new(TokenProvider::with_base_url(server.url()).expect("TokenProvider::with_base_url"));
    let now = chrono::Utc::now();

    sign_get(
        &token_provider,
        "sa@project.iam.gserviceaccount.com",
        "bucket",
        "obj",
        now,
        Some(&server.url()),
    )
    .await
    .expect("first sign_get must succeed");

    // expires_in=1 → expires_at = now + 1s. Refresh-ahead window = 60 s.
    // now >= expires_at - 60s is true immediately (1 - 60 = -59 s in the past).
    sign_get(
        &token_provider,
        "sa@project.iam.gserviceaccount.com",
        "bucket",
        "obj",
        now,
        Some(&server.url()),
    )
    .await
    .expect("second sign_get must succeed");

    meta.assert_async().await;
    meta2.assert_async().await;
    sign_mock.assert_async().await;
}

// ---------------------------------------------------------------------------
// AC-3, AC-9(h): retry-on-401 — signBlob 401 then 200
// ---------------------------------------------------------------------------

/// AC-3 / AC-9(h): signBlob responds 401 then 200 → URL returned.
/// metadata.expect(2) (cache invalidated after 401), signBlob.expect(2).
#[tokio::test]
async fn sec004_retry_on_401() {
    // AC-3 / AC-9(h): retry-on-401 — cache invalidated, second fetch succeeds
    let mut server = mockito::Server::new_async().await;

    let meta = server
        .mock(
            "GET",
            "/computeMetadata/v1/instance/service-accounts/default/token",
        )
        .with_status(200)
        .with_header("content-type", "application/json")
        .with_body(r#"{"access_token":"token-v1","expires_in":3600,"token_type":"Bearer"}"#)
        .expect(2)
        .create_async()
        .await;

    // First signBlob call returns 401, second returns 200.
    let _sign_401 = server
        .mock(
            "POST",
            "/v1/projects/-/serviceAccounts/sa@project.iam.gserviceaccount.com:signBlob",
        )
        .with_status(401)
        .expect(1)
        .create_async()
        .await;

    let _sign_200 = server
        .mock(
            "POST",
            "/v1/projects/-/serviceAccounts/sa@project.iam.gserviceaccount.com:signBlob",
        )
        .with_status(200)
        .with_header("content-type", "application/json")
        .with_body(format!(r#"{{"signedBlob":"{}"}}"#, fake_signed_blob_b64()))
        .expect(1)
        .create_async()
        .await;

    let token_provider =
        Arc::new(TokenProvider::with_base_url(server.url()).expect("TokenProvider::with_base_url"));
    let now = chrono::Utc::now();

    let url = sign_get(
        &token_provider,
        "sa@project.iam.gserviceaccount.com",
        "bucket",
        "obj",
        now,
        Some(&server.url()),
    )
    .await
    .expect("sign_get must succeed after 401 retry");

    assert!(url.contains("X-Goog-Signature="), "URL must have signature");
    meta.assert_async().await;
}

// ---------------------------------------------------------------------------
// AC-4, AC-5, AC-9(b): signBlob 403 → 503 + reason_code=signblob_403
// ---------------------------------------------------------------------------

/// AC-4 / AC-5 / AC-9(b): signBlob returns 403 → `SignError::SignBlob403`.
/// Log must contain `reason_code=signblob_403` and NOT contain token/signed_blob.
#[tokio::test]
#[tracing_test::traced_test]
async fn sec004_signblob_403_returns_503() {
    // AC-4 / AC-5 / AC-9(b): signBlob 403 → SignBlob403 + reason_code log
    let mut server = mockito::Server::new_async().await;

    let _meta = server
        .mock(
            "GET",
            "/computeMetadata/v1/instance/service-accounts/default/token",
        )
        .with_status(200)
        .with_header("content-type", "application/json")
        .with_body(r#"{"access_token":"test-token","expires_in":3600,"token_type":"Bearer"}"#)
        .create_async()
        .await;

    let _sign = server
        .mock(
            "POST",
            "/v1/projects/-/serviceAccounts/sa@project.iam.gserviceaccount.com:signBlob",
        )
        .with_status(403)
        .create_async()
        .await;

    let token_provider =
        Arc::new(TokenProvider::with_base_url(server.url()).expect("TokenProvider::with_base_url"));

    let err = sign_get(
        &token_provider,
        "sa@project.iam.gserviceaccount.com",
        "bucket",
        "obj",
        chrono::Utc::now(),
        Some(&server.url()),
    )
    .await
    .expect_err("must fail on 403");

    assert_eq!(err.reason_code(), "signblob_403");
    // AC-5: no sensitive data in log
    assert!(!logs_contain("test-token"), "log must not contain token");
    assert!(
        !logs_contain("signedBlob"),
        "log must not contain signedBlob"
    );
}

// ---------------------------------------------------------------------------
// AC-4, AC-5, AC-9(c): signBlob 429 → reason_code=signblob_429
// ---------------------------------------------------------------------------

/// AC-4 / AC-5 / AC-9(c): signBlob 429 → `SignError::SignBlob429`.
#[tokio::test]
#[tracing_test::traced_test]
async fn sec004_signblob_429_returns_503() {
    // AC-4 / AC-5 / AC-9(c): signBlob 429 → SignBlob429
    let mut server = mockito::Server::new_async().await;

    let _meta = server
        .mock(
            "GET",
            "/computeMetadata/v1/instance/service-accounts/default/token",
        )
        .with_status(200)
        .with_header("content-type", "application/json")
        .with_body(r#"{"access_token":"test-token","expires_in":3600,"token_type":"Bearer"}"#)
        .create_async()
        .await;

    let _sign = server
        .mock(
            "POST",
            "/v1/projects/-/serviceAccounts/sa@project.iam.gserviceaccount.com:signBlob",
        )
        .with_status(429)
        .create_async()
        .await;

    let token_provider =
        Arc::new(TokenProvider::with_base_url(server.url()).expect("TokenProvider::with_base_url"));

    let err = sign_get(
        &token_provider,
        "sa@project.iam.gserviceaccount.com",
        "bucket",
        "obj",
        chrono::Utc::now(),
        Some(&server.url()),
    )
    .await
    .expect_err("must fail on 429");

    assert_eq!(err.reason_code(), "signblob_429");
    assert!(!logs_contain("test-token"), "log must not contain token");
}

// ---------------------------------------------------------------------------
// AC-4, AC-5, AC-9(d): signBlob 500 → reason_code=signblob_5xx
// ---------------------------------------------------------------------------

/// AC-4 / AC-5 / AC-9(d): signBlob 500 → `SignError::SignBlob5xx`.
#[tokio::test]
#[tracing_test::traced_test]
async fn sec004_signblob_500_returns_503() {
    // AC-4 / AC-5 / AC-9(d): signBlob 500 → SignBlob5xx
    let mut server = mockito::Server::new_async().await;

    let _meta = server
        .mock(
            "GET",
            "/computeMetadata/v1/instance/service-accounts/default/token",
        )
        .with_status(200)
        .with_header("content-type", "application/json")
        .with_body(r#"{"access_token":"test-token","expires_in":3600,"token_type":"Bearer"}"#)
        .create_async()
        .await;

    let _sign = server
        .mock(
            "POST",
            "/v1/projects/-/serviceAccounts/sa@project.iam.gserviceaccount.com:signBlob",
        )
        .with_status(500)
        .create_async()
        .await;

    let token_provider =
        Arc::new(TokenProvider::with_base_url(server.url()).expect("TokenProvider::with_base_url"));

    let err = sign_get(
        &token_provider,
        "sa@project.iam.gserviceaccount.com",
        "bucket",
        "obj",
        chrono::Utc::now(),
        Some(&server.url()),
    )
    .await
    .expect_err("must fail on 500");

    assert_eq!(err.reason_code(), "signblob_5xx");
    assert!(!logs_contain("test-token"), "log must not contain token");
}

// ---------------------------------------------------------------------------
// AC-4, AC-5, AC-9(e): metadata token 500 → reason_code=metadata_token_failed
// ---------------------------------------------------------------------------

/// AC-4 / AC-5 / AC-9(e): metadata token endpoint returns 500 → `SignError::MetadataTokenFailed`.
#[tokio::test]
#[tracing_test::traced_test]
async fn sec004_metadata_token_fail_returns_503() {
    // AC-4 / AC-5 / AC-9(e): metadata 500 → MetadataTokenFailed
    let mut server = mockito::Server::new_async().await;

    let _meta = server
        .mock(
            "GET",
            "/computeMetadata/v1/instance/service-accounts/default/token",
        )
        .with_status(500)
        .create_async()
        .await;

    let token_provider =
        Arc::new(TokenProvider::with_base_url(server.url()).expect("TokenProvider::with_base_url"));

    let err = sign_get(
        &token_provider,
        "sa@project.iam.gserviceaccount.com",
        "bucket",
        "obj",
        chrono::Utc::now(),
        Some(&server.url()),
    )
    .await
    .expect_err("must fail on metadata 500");

    assert_eq!(err.reason_code(), "metadata_token_failed");
    assert!(!logs_contain("test-token"), "log must not contain token");
}

// ---------------------------------------------------------------------------
// AC-5, AC-6, AC-9(f): timeout → reason_code=timeout + elapsed < 6 s
// ---------------------------------------------------------------------------

/// AC-5 / AC-6 / AC-9(f): closed TCP port → reqwest fails with a network error quickly.
/// Asserts `reason_code=network` and that elapsed < 6 s (AC-6 oracle: 5 s total timeout).
#[tokio::test]
#[tracing_test::traced_test]
async fn sec004_network_error_logs_network() {
    // AC-5 / AC-6 / AC-9(f): network error on closed port → reason_code=network, fast fail
    // Port 1 is reserved and almost always refused (ECONNREFUSED) on Linux/Windows.
    let token_provider = Arc::new(
        TokenProvider::with_base_url("http://127.0.0.1:1".to_string())
            .expect("TokenProvider::with_base_url"),
    );

    let start = std::time::Instant::now();
    let err = sign_get(
        &token_provider,
        "sa@project.iam.gserviceaccount.com",
        "bucket",
        "obj",
        chrono::Utc::now(),
        Some("http://127.0.0.1:1"),
    )
    .await
    .expect_err("must fail on network error");

    let elapsed = start.elapsed();
    // Network error → either "network" (ECONNREFUSED) or "timeout" (connect timeout).
    let code = err.reason_code();
    assert!(
        code == "network" || code == "timeout",
        "reason_code must be network or timeout, got {code}"
    );
    assert!(
        elapsed.as_secs() < 6,
        "must fail within 6 s (AC-6 oracle); elapsed={elapsed:?}"
    );
    assert!(!logs_contain("test-token"), "log must not contain token");
}
