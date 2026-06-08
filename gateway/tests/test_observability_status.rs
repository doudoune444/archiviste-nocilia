//! Integration + contract tests for OBS-002.
//!
//! AC-1: body shape {status, dependencies: {postgres,gcs,workers}, checked_at}, 3 keys, no mistral.
//! AC-2: each dep has exactly {status, latency_ms}; status ∈ {ok,down}; no host/url/message.
//! AC-3: property — status==ok ⟺ all 3 deps ok (table-driven, 8 combinations, local only).
//! AC-5: 200 OK even when degraded.
//! AC-6: never 5xx — all-down → 200 degraded.
//! AC-7: anonymous, no auth guard — contract via include_str!.
//! AC-8: probe timeout → down, latency_ms ≲ 2200 ms.
//! AC-9: all 3 probes in parallel — all-down snapshot fresh < 3s wall-clock.
//! AC-10: cache TTL 10s — 2 calls <10s → same checked_at.
//! AC-11: probe error → down + no detail in body.
//! AC-12: workers probe — 2xx → ok, else down.
//! AC-15: 5 security headers present byte-for-byte on GET /v1/status.

#![allow(clippy::unwrap_used)]
#![allow(clippy::expect_used)]
// Test doc comments use AC-notation with unquoted identifiers — not production docs.
#![allow(clippy::doc_markdown)]

mod common;
use common::jwt_helpers::{make_app_state, make_test_config};

use archiviste_gateway::{auth_metadata::IdTokenProvider, router, state::AppState};
use axum::{
    body::Body,
    http::{Request, StatusCode},
};
use http_body_util::BodyExt;
use std::sync::Arc;
use tower::ServiceExt;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

fn make_state() -> Arc<AppState> {
    // db_pool=None → postgres probe → down (deterministic in test env).
    make_app_state("http://127.0.0.1:1")
}

async fn get_anon(app: axum::Router, uri: &str) -> axum::response::Response {
    app.oneshot(
        Request::builder()
            .method("GET")
            .uri(uri)
            .body(Body::empty())
            .unwrap(),
    )
    .await
    .unwrap()
}

async fn body_json(resp: axum::response::Response) -> serde_json::Value {
    let bytes = resp.into_body().collect().await.unwrap().to_bytes();
    serde_json::from_slice(&bytes).expect("response body must be valid JSON")
}

/// Literal CSP value — must be identical to lib.rs CSP const (AC-15).
const CSP_VALUE: &str = "default-src 'self'; script-src 'self'; style-src 'self'; \
    img-src 'self' data:; object-src 'none'; frame-ancestors 'none'; \
    base-uri 'none'; form-action 'self'";

/// Literal HSTS value (AC-15, SEC-003 AC-1).
const HSTS_VALUE: &str = "max-age=31536000; includeSubDomains; preload";

/// Assert all 5 security headers are present byte-for-byte (AC-15).
fn assert_security_headers(resp: &axum::response::Response) {
    let h = resp.headers();
    assert_eq!(
        h.get("strict-transport-security")
            .unwrap()
            .to_str()
            .unwrap(),
        HSTS_VALUE,
        "HSTS mismatch"
    );
    assert_eq!(
        h.get("content-security-policy").unwrap().to_str().unwrap(),
        CSP_VALUE,
        "AC-15: CSP must not be modified"
    );
    assert_eq!(
        h.get("x-content-type-options").unwrap().to_str().unwrap(),
        "nosniff"
    );
    assert_eq!(
        h.get("referrer-policy").unwrap().to_str().unwrap(),
        "strict-origin-when-cross-origin"
    );
    assert_eq!(h.get("x-frame-options").unwrap().to_str().unwrap(), "DENY");
}

// ---------------------------------------------------------------------------
// AC-1 / AC-2 / AC-5 (happy-path shape + sanitisation + 200)
// ---------------------------------------------------------------------------

/// AC-1: GET /v1/status → 200, content-type application/json, body has 3 deps + no mistral.
/// AC-2: each dep has exactly status + latency_ms; status ∈ {ok,down}; no host/url/msg.
/// AC-5: 200 OK even when all deps down (db_pool=None → degraded).
#[tokio::test]
async fn ac1_ac2_ac5_status_shape_200_sanitised() {
    // AC-1/AC-2/AC-5: db_pool=None → all probes → down → degraded → 200.
    let app = router(make_state());
    let resp = get_anon(app, "/v1/status").await;

    assert_eq!(
        resp.status(),
        StatusCode::OK,
        "AC-5: must be 200 even degraded"
    );

    let ct = resp
        .headers()
        .get("content-type")
        .expect("content-type missing")
        .to_str()
        .unwrap();
    assert!(
        ct.starts_with("application/json"),
        "AC-1: content-type must be JSON, got: {ct}"
    );

    let json = body_json(resp).await;

    // AC-1: root keys.
    let status = json["status"].as_str().expect("status must be string");
    assert!(
        status == "ok" || status == "degraded",
        "AC-1: status must be ok or degraded, got: {status}"
    );
    assert!(
        json["checked_at"].as_str().is_some(),
        "AC-1: checked_at must be present"
    );

    // AC-1: 3 named deps present, no mistral.
    let deps = json["dependencies"]
        .as_object()
        .expect("dependencies must be object");
    assert!(deps.contains_key("postgres"), "AC-1: postgres dep missing");
    assert!(deps.contains_key("gcs"), "AC-1: gcs dep missing");
    assert!(deps.contains_key("workers"), "AC-1: workers dep missing");
    assert!(
        !deps.contains_key("mistral"),
        "AC-1: mistral must NOT be present"
    );

    // AC-1: root object has exactly 3 keys: status, dependencies, checked_at.
    assert_eq!(
        json.as_object().unwrap().len(),
        3,
        "AC-1: root must have exactly 3 keys"
    );

    // AC-2: each dep has exactly {status, latency_ms}, no other key, status ∈ {ok,down}.
    for (dep_name, dep_val) in deps {
        let dep_obj = dep_val.as_object().expect("dep must be object");
        assert_eq!(
            dep_obj.len(),
            2,
            "AC-2: dep {dep_name} must have exactly 2 keys"
        );
        let dep_status = dep_obj["status"]
            .as_str()
            .expect("dep.status must be string");
        assert!(
            dep_status == "ok" || dep_status == "down",
            "AC-2: {dep_name}.status must be ok|down, got: {dep_status}"
        );
        // latency_ms must be a non-negative integer.
        dep_obj["latency_ms"]
            .as_u64()
            .expect("latency_ms must be u64");
    }

    // AC-2: body string must not contain host/url/message/SQL leak.
    let body_str = json.to_string().to_lowercase();
    assert!(!body_str.contains("postgres://"), "AC-2: no DB URL in body");
    assert!(
        !body_str.contains("storage.googleapis.com"),
        "AC-2: no GCS host in body"
    );
    assert!(!body_str.contains("select"), "AC-2: no SQL in body");
    assert!(
        !body_str.contains("127.0.0.1"),
        "AC-2: no internal host in body"
    );
}

// ---------------------------------------------------------------------------
// AC-3 (property — table-driven, 8 combinations, local only)
// ---------------------------------------------------------------------------

/// AC-3: status_root == "ok" ⟺ all 3 deps "ok"; ≥1 down ⟹ "degraded".
/// Table-driven across all 8 combinations (not in specs/properties.md — local invariant only).
#[test]
fn ac3_aggregate_status_property_table() {
    use archiviste_gateway::handlers::status::{aggregate_status, DepStatus};

    // (postgres, gcs, workers) → expected root status.
    let cases: &[(&str, &str, &str, &str)] = &[
        ("ok", "ok", "ok", "ok"),
        ("down", "ok", "ok", "degraded"),
        ("ok", "down", "ok", "degraded"),
        ("ok", "ok", "down", "degraded"),
        ("down", "down", "ok", "degraded"),
        ("down", "ok", "down", "degraded"),
        ("ok", "down", "down", "degraded"),
        ("down", "down", "down", "degraded"),
    ];

    for &(pg_s, gcs_s, wrk_s, expected) in cases {
        let postgres = DepStatus {
            status: pg_s,
            latency_ms: 0,
        };
        let gcs = DepStatus {
            status: gcs_s,
            latency_ms: 0,
        };
        let workers = DepStatus {
            status: wrk_s,
            latency_ms: 0,
        };
        let result = aggregate_status(&postgres, &gcs, &workers);
        assert_eq!(
            result, expected,
            "AC-3: aggregate({pg_s},{gcs_s},{wrk_s}) expected {expected}, got {result}"
        );
        // AC-3 exact property: ok ⟺ all three ok.
        let all_ok = pg_s == "ok" && gcs_s == "ok" && wrk_s == "ok";
        if all_ok {
            assert_eq!(result, "ok", "AC-3 property: all ok → root ok");
        } else {
            assert_eq!(
                result, "degraded",
                "AC-3 property: any down → root degraded"
            );
        }
    }
}

// ---------------------------------------------------------------------------
// AC-6: never 5xx — all down → 200 degraded
// ---------------------------------------------------------------------------

/// AC-6: all deps down (no pool, workers unreachable) → 200 with degraded.
#[tokio::test]
async fn ac6_all_down_returns_200_degraded() {
    // AC-6: db_pool=None + workers at 127.0.0.1:1 (unreachable) → all probes down.
    let app = router(make_state());
    let resp = get_anon(app, "/v1/status").await;
    assert_eq!(resp.status(), StatusCode::OK, "AC-6: must be 200 never 5xx");
    let json = body_json(resp).await;
    assert_eq!(json["status"], "degraded", "AC-6: all down → degraded");
}

// ---------------------------------------------------------------------------
// AC-7 (contract): /v1/status in public_api, no auth guard
// ---------------------------------------------------------------------------

/// AC-7: /v1/status is anonymous — in lib.rs public_api, no RequireAuthor/RequireMember.
#[test]
fn ac7_contract_status_no_auth_guard() {
    // AC-7: inspect source at compile time.
    let lib_src = include_str!("../src/lib.rs");
    assert!(
        lib_src.contains("/v1/status"),
        "AC-7: /v1/status must be in lib.rs"
    );
    assert!(
        lib_src.contains("public_api"),
        "AC-7: public_api must exist in lib.rs"
    );

    let handler_src = include_str!("../src/handlers/status.rs");
    assert!(
        !handler_src.contains("RequireAuthor"),
        "AC-7: status.rs must not contain RequireAuthor"
    );
    assert!(
        !handler_src.contains("RequireMember"),
        "AC-7: status.rs must not contain RequireMember"
    );
    assert!(
        !handler_src.contains("author_only") && !handler_src.contains("member_only"),
        "AC-7: status.rs must not contain auth guard markers"
    );
}

// ---------------------------------------------------------------------------
// AC-8 / AC-9: timeout + parallel (TCP listener that never responds)
// ---------------------------------------------------------------------------

/// AC-8 / AC-9: all 3 probes time out simultaneously → all down, wall-clock < 3.5s.
///
/// Uses a TCP listener that accepts connections but never sends data, so the
/// workers HTTP probe hangs until the 2s `PROBE_TIMEOUT` fires.  Other probes
/// (postgres=None, gcs=metadata unreachable) also time out fast.
/// AC-9: because probes run in parallel the total is bounded by one timeout (~2s),
/// not three (~6s).
#[tokio::test]
async fn ac8_ac9_all_probes_timeout_bounded_wall_clock() {
    use std::time::Instant;
    use tokio::net::TcpListener;

    // Bind a TCP listener that accepts connections but never responds (reads hang).
    let listener = TcpListener::bind("127.0.0.1:0").await.expect("bind");
    let addr = listener.local_addr().expect("local_addr");
    // Keep the listener alive so the port stays open (connections accepted, never answered).
    tokio::spawn(async move {
        loop {
            // Accept but intentionally never write — probe will hit PROBE_TIMEOUT.
            if listener.accept().await.is_err() {
                break;
            }
        }
    });

    let workers_url = format!("http://{addr}");
    let mut config = make_test_config(&workers_url);
    config.workers_url = workers_url.clone();
    // Short connect timeout so the HTTP client doesn't stall on connect.
    config.connect_timeout_ms = 500;
    // request_timeout > PROBE_TIMEOUT (2s) so timeout is driven by our handler, not reqwest.
    config.request_timeout_ms = 5_000;

    let id_provider =
        Arc::new(IdTokenProvider::new_stub_always_valid().expect("stub IdTokenProvider"));
    let state =
        Arc::new(AppState::new_with_id_token_provider(config, id_provider).expect("AppState"));

    let t0 = Instant::now();
    let app = router(Arc::clone(&state));
    let resp = get_anon(app, "/v1/status").await;
    let elapsed_ms = t0.elapsed().as_millis();

    // AC-5/AC-6: 200 always.
    assert_eq!(resp.status(), StatusCode::OK, "AC-8: must be 200");
    // AC-9: parallel probes → total < 3.5s (not ~6s serial).
    assert!(
        elapsed_ms < 3_500,
        "AC-9: parallel probes must finish < 3.5s, took {elapsed_ms}ms"
    );

    let json = body_json(resp).await;
    // All deps down (postgres=None, gcs token server unreachable, workers=timeout).
    assert_eq!(json["status"], "degraded", "AC-6: all down → degraded");
    let workers_dep = &json["dependencies"]["workers"];
    assert_eq!(
        workers_dep["status"], "down",
        "AC-8: timed-out probe must be down"
    );
    let latency_ms = workers_dep["latency_ms"].as_u64().unwrap();
    // AC-8: latency_ms bounded by PROBE_TIMEOUT (2000ms) + small margin.
    assert!(
        latency_ms <= 2_200,
        "AC-8: latency_ms must be ≲ 2200, got {latency_ms}"
    );
}

// ---------------------------------------------------------------------------
// AC-10: cache TTL 10s
// ---------------------------------------------------------------------------

/// AC-10: two calls < 10s → same checked_at (served from cache, probes not re-run).
#[tokio::test]
async fn ac10_two_calls_within_ttl_same_checked_at() {
    // AC-10: use shared AppState so both requests share the same HealthSnapshotCache.
    let state = make_state();
    let app1 = router(Arc::clone(&state));
    let app2 = router(Arc::clone(&state));

    let resp1 = get_anon(app1, "/v1/status").await;
    let json1 = body_json(resp1).await;
    let checked_at1 = json1["checked_at"].as_str().unwrap().to_string();

    // Second call immediately after (well within 10s TTL).
    let resp2 = get_anon(app2, "/v1/status").await;
    let json2 = body_json(resp2).await;
    let checked_at2 = json2["checked_at"].as_str().unwrap().to_string();

    assert_eq!(
        checked_at1, checked_at2,
        "AC-10: two calls < 10s must return same checked_at (cache hit)"
    );
}

// ---------------------------------------------------------------------------
// AC-11: probe error → down, no detail in body
// ---------------------------------------------------------------------------

/// AC-11: workers probe error → down, body contains no URL/host/message detail.
#[tokio::test]
async fn ac11_probe_error_down_no_detail_in_body() {
    // AC-11: workers_url=127.0.0.1:1 (connection refused) → workers probe down.
    // db_pool=None → postgres probe down.
    let app = router(make_state());
    let resp = get_anon(app, "/v1/status").await;

    assert_eq!(resp.status(), StatusCode::OK);
    let json = body_json(resp).await;

    // Probes are down but body must not contain error details (AC-2 / AC-11).
    let body_str = json.to_string();
    assert!(
        !body_str.contains("connection refused"),
        "AC-11: body must not contain error detail"
    );
    assert!(
        !body_str.contains("127.0.0.1"),
        "AC-11: body must not contain internal host"
    );
    // Root body should only have "status", "dependencies", "checked_at" (no "error" key).
    assert!(
        !json.as_object().unwrap().contains_key("error"),
        "AC-11: body must not contain 'error' key"
    );
}

// ---------------------------------------------------------------------------
// AC-12: workers probe 2xx → ok
// ---------------------------------------------------------------------------

/// AC-12: workers endpoint responds 2xx → workers.status="ok".
#[tokio::test]
async fn ac12_workers_2xx_is_ok() {
    // AC-12: mockito /health returns 200 → workers probe = ok.
    let mut server = mockito::Server::new_async().await;
    let _mock = server
        .mock("GET", "/health")
        .with_status(200)
        .with_body("{}")
        .create_async()
        .await;

    let config = make_test_config(&server.url());
    let id_provider =
        Arc::new(IdTokenProvider::new_stub_always_valid().expect("stub IdTokenProvider"));
    let state =
        Arc::new(AppState::new_with_id_token_provider(config, id_provider).expect("AppState"));

    let app = router(state);
    let resp = get_anon(app, "/v1/status").await;
    assert_eq!(resp.status(), StatusCode::OK);
    let json = body_json(resp).await;
    assert_eq!(
        json["dependencies"]["workers"]["status"], "ok",
        "AC-12: workers 2xx → ok"
    );
}

/// AC-12: workers endpoint responds non-2xx → workers.status="down".
#[tokio::test]
async fn ac12_workers_non_2xx_is_down() {
    // AC-12: mockito /health returns 503 → workers probe = down.
    let mut server = mockito::Server::new_async().await;
    let _mock = server
        .mock("GET", "/health")
        .with_status(503)
        .create_async()
        .await;

    let config = make_test_config(&server.url());
    let id_provider =
        Arc::new(IdTokenProvider::new_stub_always_valid().expect("stub IdTokenProvider"));
    let state =
        Arc::new(AppState::new_with_id_token_provider(config, id_provider).expect("AppState"));

    let app = router(state);
    let resp = get_anon(app, "/v1/status").await;
    assert_eq!(resp.status(), StatusCode::OK);
    let json = body_json(resp).await;
    assert_eq!(
        json["dependencies"]["workers"]["status"], "down",
        "AC-12: workers non-2xx → down"
    );
}

// ---------------------------------------------------------------------------
// AC-15: 5 security headers on GET /v1/status
// ---------------------------------------------------------------------------

/// AC-15: GET /v1/status has all 5 security headers byte-for-byte; CSP unchanged.
#[tokio::test]
async fn ac15_status_security_headers() {
    // AC-15: router-wide security headers must be present on /v1/status.
    let app = router(make_state());
    let resp = get_anon(app, "/v1/status").await;
    assert_eq!(resp.status(), StatusCode::OK);
    assert_security_headers(&resp);
}
