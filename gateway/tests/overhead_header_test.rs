//! Integration tests for `X-Gateway-Overhead-Ms` timing header (OPS-001a).
//!
//! # AC references
//! - OPS-001 AC-4: `X-Gateway-Overhead-Ms` header present on 200 responses, integer ms.
//! - Plan §42-44 (test strategy):
//!   (a) header present on 200,
//!   (b) value is a valid integer,
//!   (c) header value < total elapsed (overhead < round-trip),
//!   (d) `total_elapsed` − header ≈ workers delay ± margin,
//!   (e) header present on 400 (validation failure path).

#![allow(clippy::unwrap_used, clippy::expect_used)]

mod common;
use common::jwt_helpers::make_test_config;

use archiviste_gateway::{router, state::AppState};
use axum::body::Body;
use axum::http::{Request, StatusCode};
use std::sync::Arc;
use std::time::Instant;
use tower::ServiceExt;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

fn make_state(workers_url: &str) -> Arc<AppState> {
    Arc::new(AppState::new(make_test_config(workers_url)).unwrap())
}

async fn post_chat_raw(app: axum::Router, body: &str) -> axum::response::Response {
    app.oneshot(
        Request::builder()
            .method("POST")
            .uri("/v1/chat")
            .header("content-type", "application/json")
            .body(Body::from(body.to_string()))
            .unwrap(),
    )
    .await
    .unwrap()
}

/// Parse `X-Gateway-Overhead-Ms` header as `u64`.
///
/// Returns `None` if absent or not parseable.
fn parse_overhead_header(resp: &axum::response::Response) -> Option<u64> {
    resp.headers()
        .get("x-gateway-overhead-ms")
        .and_then(|v| v.to_str().ok())
        .and_then(|s| s.parse::<u64>().ok())
}

// ---------------------------------------------------------------------------
// AC-4 (a): header present on 200 response
// ---------------------------------------------------------------------------

/// OPS-001 AC-4 (a): `X-Gateway-Overhead-Ms` is present on a 200 response
/// from a successful workers call.
#[tokio::test]
async fn ac4a_overhead_header_present_on_200() {
    let mut server = mockito::Server::new_async().await;
    let _mock = server
        .mock("POST", "/v1/generate")
        .with_status(200)
        .with_header("content-type", "application/json")
        .with_body(r#"{"answer":"ok","citations":[]}"#)
        .create_async()
        .await;

    let app = router(make_state(&server.url()));
    let resp = post_chat_raw(app, r#"{"query":"Qu'est-ce que le scriptorium?"}"#).await;

    assert_eq!(resp.status(), StatusCode::OK);
    assert!(
        resp.headers().contains_key("x-gateway-overhead-ms"),
        "X-Gateway-Overhead-Ms must be present on 200"
    );
}

// ---------------------------------------------------------------------------
// AC-4 (b): header value is a valid integer
// ---------------------------------------------------------------------------

/// OPS-001 AC-4 (b): `X-Gateway-Overhead-Ms` value parses as a valid u64.
#[tokio::test]
async fn ac4b_overhead_header_is_integer() {
    let mut server = mockito::Server::new_async().await;
    let _mock = server
        .mock("POST", "/v1/generate")
        .with_status(200)
        .with_header("content-type", "application/json")
        .with_body(r#"{"answer":"ok","citations":[]}"#)
        .create_async()
        .await;

    let app = router(make_state(&server.url()));
    let resp = post_chat_raw(app, r#"{"query":"Qu'est-ce que le scriptorium?"}"#).await;

    assert_eq!(resp.status(), StatusCode::OK);
    let overhead =
        parse_overhead_header(&resp).expect("X-Gateway-Overhead-Ms must be a valid u64 integer");
    // Sanity: a gateway test response should be well under 10 seconds.
    assert!(
        overhead < 10_000,
        "overhead {overhead} ms seems implausibly large"
    );
}

// ---------------------------------------------------------------------------
// AC-4 (c): header value < total elapsed (overhead ≤ round-trip)
// ---------------------------------------------------------------------------

/// OPS-001 AC-4 (c): the reported overhead is strictly less than the total
/// elapsed time observed by the test (which includes the workers call).
#[tokio::test]
async fn ac4c_overhead_less_than_total_elapsed() {
    // Introduce a 200 ms workers delay to make the assertion meaningful.
    let mut server = mockito::Server::new_async().await;
    let _mock = server
        .mock("POST", "/v1/generate")
        .with_status(200)
        .with_header("content-type", "application/json")
        .with_chunked_body(|w| {
            std::thread::sleep(std::time::Duration::from_millis(200));
            std::io::Write::write_all(w, b"{\"answer\":\"ok\",\"citations\":[]}")
        })
        .create_async()
        .await;

    let app = router(make_state(&server.url()));

    let t0 = Instant::now();
    let resp = post_chat_raw(app, r#"{"query":"Qu'est-ce que le scriptorium?"}"#).await;
    let total_elapsed_ms = u64::try_from(t0.elapsed().as_millis()).unwrap_or(u64::MAX);

    assert_eq!(resp.status(), StatusCode::OK);
    let overhead = parse_overhead_header(&resp).expect("X-Gateway-Overhead-Ms must be present");

    // Overhead (gateway-only) must be strictly less than total elapsed (includes workers).
    assert!(
        overhead < total_elapsed_ms,
        "overhead {overhead} ms must be < total elapsed {total_elapsed_ms} ms"
    );
}

// ---------------------------------------------------------------------------
// AC-4 (d): total_elapsed − overhead ≈ workers delay ± margin
// ---------------------------------------------------------------------------

/// OPS-001 AC-4 (d): the gap between total elapsed and reported overhead
/// approximates the mocked workers delay (200 ms ± 150 ms tolerance for CI jitter).
#[tokio::test]
async fn ac4d_overhead_gap_approximates_workers_delay() {
    const WORKERS_DELAY_MS: u64 = 200;
    // Generous tolerance for CI variability (thread scheduler, test overhead).
    const MARGIN_MS: u64 = 150;

    let mut server = mockito::Server::new_async().await;
    let _mock = server
        .mock("POST", "/v1/generate")
        .with_status(200)
        .with_header("content-type", "application/json")
        .with_chunked_body(|w| {
            std::thread::sleep(std::time::Duration::from_millis(200));
            std::io::Write::write_all(w, b"{\"answer\":\"ok\",\"citations\":[]}")
        })
        .create_async()
        .await;

    let app = router(make_state(&server.url()));

    let t0 = Instant::now();
    let resp = post_chat_raw(app, r#"{"query":"Qu'est-ce que le scriptorium?"}"#).await;
    let total_elapsed_ms = u64::try_from(t0.elapsed().as_millis()).unwrap_or(u64::MAX);

    assert_eq!(resp.status(), StatusCode::OK);
    let overhead = parse_overhead_header(&resp).expect("X-Gateway-Overhead-Ms must be present");

    let gap = total_elapsed_ms.saturating_sub(overhead);
    let lower = WORKERS_DELAY_MS.saturating_sub(MARGIN_MS);
    let upper = WORKERS_DELAY_MS + MARGIN_MS;

    assert!(
        gap >= lower && gap <= upper,
        "gap between total elapsed and overhead ({gap} ms) should approximate \
         workers delay {WORKERS_DELAY_MS} ms ± {MARGIN_MS} ms; \
         total={total_elapsed_ms} ms, overhead={overhead} ms"
    );
}

// ---------------------------------------------------------------------------
// AC-4 (e): header present on 400 (validation failure)
// ---------------------------------------------------------------------------

/// OPS-001 AC-4 (e): `X-Gateway-Overhead-Ms` is present even on 400 responses
/// where no workers call is made (plan §39: "Header posé quand même").
#[tokio::test]
async fn ac4e_overhead_header_present_on_400() {
    // Port 1 refuses connections — no workers call happens on validation failure.
    let app = router(make_state("http://127.0.0.1:1"));
    let resp = post_chat_raw(app, r"{}").await;

    assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
    assert!(
        resp.headers().contains_key("x-gateway-overhead-ms"),
        "X-Gateway-Overhead-Ms must be present even on 400 (no workers call)"
    );
}
