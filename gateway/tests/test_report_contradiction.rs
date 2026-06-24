//! Integration tests for `POST /v1/report-contradiction` (CTR-002).
//!
//! AC references:
//! - AC-1: valid report → gateway forwards to workers `/v1/verify-contradiction`
//!   with `x-user-id` + `x-user-tier` headers propagated; `request_id` NOT taken from client.
//! - AC-2: passthrough of the 200 verdict body.
//! - AC-3: invalid body (empty claim / bad uuid / empty citations / too many) → 400 `invalid_request`.
//! - AC-4: upstream 4xx/5xx → 502 `upstream_error`; connect failure → 503.
//! - A01/IDOR: `conversation_id` not owned by caller → 404 `conversation_not_found`, workers not called.

#![allow(clippy::unwrap_used)]

mod common;
use common::jwt_helpers::{make_app_state, test_private_key_pem, test_public_key_pem, TEST_KEY_ID};

use archiviste_gateway::{
    auth::fingerprint::{cookie_uuid_to_user_id, ANON_COOKIE_NAME},
    router,
    state::AppState,
};
use axum::body::Body;
use axum::http::{Request, StatusCode};
use http_body_util::BodyExt;
use secrecy::SecretString;
use std::sync::Arc;
use tower::ServiceExt;
use uuid::Uuid;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

fn make_state(workers_url: &str) -> Arc<archiviste_gateway::state::AppState> {
    make_app_state(workers_url)
}

/// Build a state with a tight `request_timeout_ms` and an attached pool for timeout tests.
///
/// `connect_timeout_ms` is short (50 ms) so that a loopback-connect never
/// races with the read-side timeout. `request_timeout_ms` (500 ms) is the
/// timeout the test actually exercises.
fn make_state_with_short_timeout_and_pool(
    workers_url: &str,
    pool: sqlx::PgPool,
) -> Arc<archiviste_gateway::state::AppState> {
    let mut config = make_config_db();
    config.workers_url = workers_url.to_string();
    config.connect_timeout_ms = 50;
    config.request_timeout_ms = 500;
    // Stub workers ID-token (no metadata-server call) + attached pool: the forwarding
    // path needs a valid bearer, the A01 ownership check needs the pool. `new_with_pool`
    // alone wires the REAL metadata provider, whose fetch fails in CI → spurious 503.
    let id_token_provider = Arc::new(
        archiviste_gateway::auth_metadata::IdTokenProvider::new_stub_always_valid().unwrap(),
    );
    let mut state = AppState::new_with_id_token_provider(config, id_token_provider).unwrap();
    state.db_pool = Some(pool);
    Arc::new(state)
}

async fn post_report(app: axum::Router, body: &str) -> axum::response::Response {
    app.oneshot(
        Request::builder()
            .method("POST")
            .uri("/v1/report-contradiction")
            .header("content-type", "application/json")
            .body(Body::from(body.to_string()))
            .unwrap(),
    )
    .await
    .unwrap()
}

async fn body_json(resp: axum::response::Response) -> serde_json::Value {
    let bytes = resp.into_body().collect().await.unwrap().to_bytes();
    serde_json::from_slice(&bytes).unwrap()
}

fn assert_error_envelope(body: &serde_json::Value, expected_code: &str) {
    assert_eq!(body["error"], expected_code);
    let rid = body["request_id"].as_str().unwrap_or("");
    assert_eq!(rid.len(), 36, "request_id must be 36 chars");
    let parts: Vec<&str> = rid.split('-').collect();
    assert_eq!(parts.len(), 5);
}

const VALID_UUID: &str = "550e8400-e29b-41d4-a716-446655440000";

// ---------------------------------------------------------------------------
// AC-1 / AC-2: happy path — forwards to workers, passthrough body, headers propagated
// ---------------------------------------------------------------------------

/// AC-1: valid report → gateway forwards to `/v1/verify-contradiction` with
/// `x-user-id` and `x-user-tier` headers, and the gateway-generated `request_id`
/// (never the client-supplied one).
/// AC-2: workers 200 → passthrough verdict body.
///
/// DB-backed: ownership check is fail-closed (pool None → 503), so we seed an
/// anon user + owned conversation and request as that owner.
#[sqlx::test(migrations = "../migrations")]
async fn ac1_ac2_valid_report_forwarded_to_workers(pool: sqlx::PgPool) {
    use std::sync::{Arc as StdArc, Mutex};

    let captured_tier: StdArc<Mutex<Option<String>>> = StdArc::new(Mutex::new(None));
    let captured_uid: StdArc<Mutex<Option<String>>> = StdArc::new(Mutex::new(None));
    let captured_req_id: StdArc<Mutex<Option<String>>> = StdArc::new(Mutex::new(None));

    let mut server = mockito::Server::new_async().await;
    let tier_cap = StdArc::clone(&captured_tier);
    let uid_cap = StdArc::clone(&captured_uid);
    let rid_cap = StdArc::clone(&captured_req_id);

    // AC-2 (#172): outcome field added to workers response; gateway must pass it through byte-for-byte.
    let verdict_body = r#"{"verdict":"contradiction","reason":"Sources en désaccord.","ticket_action":"created","ticket_id":"550e8400-e29b-41d4-a716-446655440001","outcome":"confirmed"}"#;

    let _mock = server
        .mock("POST", "/v1/verify-contradiction")
        .with_status(200)
        .with_header("content-type", "application/json")
        .with_body_from_request(move |req| {
            if let Some(t) = req.header("x-user-tier").first() {
                *tier_cap.lock().unwrap() = Some(t.to_str().unwrap_or("").to_string());
            }
            if let Some(u) = req.header("x-user-id").first() {
                *uid_cap.lock().unwrap() = Some(u.to_str().unwrap_or("").to_string());
            }
            if let Some(r) = req.header("x-request-id").first() {
                *rid_cap.lock().unwrap() = Some(r.to_str().unwrap_or("").to_string());
            }
            verdict_body.into()
        })
        .create_async()
        .await;

    let (caller_cookie, caller_id) = seed_anon_user(&pool).await;
    let conv_id = seed_owned_conversation(&pool, caller_id).await;

    let app = router(make_state_with_db_pool(&server.url(), pool));
    let resp = post_report_as_anon(app, caller_cookie, conv_id).await;

    // AC-2: passthrough 200 body (new clean-break shape: verdict + reason, #162)
    // AC-2 (#172): outcome field survives gateway passthrough byte-for-byte.
    assert_eq!(resp.status(), StatusCode::OK);
    let body = body_json(resp).await;
    assert_eq!(body["verdict"], "contradiction");
    assert_eq!(body["ticket_action"], "created");
    assert_eq!(
        body["outcome"], "confirmed",
        "outcome must survive gateway passthrough"
    );

    // AC-1: identity headers forwarded
    let tier = captured_tier.lock().unwrap().clone().unwrap_or_default();
    assert_eq!(tier, "anonymous", "x-user-tier must be anonymous");
    let uid = captured_uid.lock().unwrap().clone().unwrap_or_default();
    assert_eq!(uid.len(), 36, "x-user-id must be a UUID");

    // AC-1: gateway-generated request_id (not from client)
    let req_id_sent = captured_req_id.lock().unwrap().clone().unwrap_or_default();
    assert_eq!(
        req_id_sent.len(),
        36,
        "x-request-id forwarded to workers must be a UUID"
    );
}

// ---------------------------------------------------------------------------
// AC-3: validation failures → 400 invalid_request, workers NOT called
// ---------------------------------------------------------------------------

/// AC-3: empty claim → 400 `invalid_request`.
#[tokio::test]
async fn ac3_empty_claim_returns_400() {
    // Port 1 = loopback refuse — workers must NOT be called.
    let app = router(make_state("http://127.0.0.1:1"));
    let payload = format!(
        r#"{{"claim":"","conversation_id":"{VALID_UUID}","citations":[{{"source_path":"f.md","chunk_ords":[0]}}]}}"#
    );
    let resp = post_report(app, &payload).await;
    assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
    let body = body_json(resp).await;
    assert_error_envelope(&body, "invalid_request");
}

/// AC-3: claim > 4096 bytes → 400.
#[tokio::test]
async fn ac3_claim_too_long_returns_400() {
    let long_claim = "a".repeat(4097);
    let payload = format!(
        r#"{{"claim":"{long_claim}","conversation_id":"{VALID_UUID}","citations":[{{"source_path":"f.md","chunk_ords":[0]}}]}}"#
    );
    let app = router(make_state("http://127.0.0.1:1"));
    let resp = post_report(app, &payload).await;
    assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
    let body = body_json(resp).await;
    assert_error_envelope(&body, "invalid_request");
}

/// AC-3: bad `conversation_id` (not a UUID) → 400.
#[tokio::test]
async fn ac3_bad_conversation_id_returns_400() {
    let payload = r#"{"claim":"x","conversation_id":"not-a-uuid","citations":[{"source_path":"f.md","chunk_ords":[0]}]}"#;
    let app = router(make_state("http://127.0.0.1:1"));
    let resp = post_report(app, payload).await;
    assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
    let body = body_json(resp).await;
    assert_error_envelope(&body, "invalid_request");
}

/// No-citation path (#162): empty citations array → forwarded to workers with
/// `citations: []` in the body (not omitted).
///
/// DB-backed: ownership check is fail-closed; seeded owner posts the request.
#[sqlx::test(migrations = "../migrations")]
async fn no_citation_path_empty_citations_accepted(pool: sqlx::PgPool) {
    use std::sync::{Arc as StdArc, Mutex};

    let captured_body: StdArc<Mutex<Option<serde_json::Value>>> = StdArc::new(Mutex::new(None));
    let body_cap = StdArc::clone(&captured_body);

    let mut server = mockito::Server::new_async().await;
    let _mock = server
        .mock("POST", "/v1/verify-contradiction")
        .with_status(200)
        .with_header("content-type", "application/json")
        .with_body_from_request(move |req| {
            if let Ok(bytes) = req.body() {
                if let Ok(v) = serde_json::from_slice::<serde_json::Value>(bytes) {
                    *body_cap.lock().unwrap() = Some(v);
                }
            }
            r#"{"verdict":"present","reason":"ok","ticket_action":"not_raised","ticket_id":null}"#
                .into()
        })
        .create_async()
        .await;

    let (caller_cookie, caller_id) = seed_anon_user(&pool).await;
    let conv_id = seed_owned_conversation(&pool, caller_id).await;

    // Manually build payload with empty citations to test that specific path.
    let payload = format!(r#"{{"claim":"x","conversation_id":"{conv_id}","citations":[]}}"#);
    let app = router(make_state_with_db_pool(&server.url(), pool));
    let resp = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/v1/report-contradiction")
                .header("content-type", "application/json")
                .header("cookie", format!("{ANON_COOKIE_NAME}={caller_cookie}"))
                .body(Body::from(payload))
                .unwrap(),
        )
        .await
        .unwrap();

    // Gateway must accept empty citations and forward (not 400).
    assert_ne!(resp.status(), StatusCode::BAD_REQUEST);
    assert_eq!(resp.status(), StatusCode::OK);

    // build_workers_body keeps citations present-and-empty when the array is [].
    let forwarded = captured_body.lock().unwrap().clone().unwrap();
    assert_eq!(
        forwarded["citations"],
        serde_json::json!([]),
        "empty citations array must be forwarded as present-and-empty"
    );
}

/// No-citation path (#162): absent citations field → forwarded to workers
/// without the `citations` key (omitted, not null), triggering retrieval path in workers.
///
/// DB-backed: ownership check is fail-closed; seeded owner posts the request.
#[sqlx::test(migrations = "../migrations")]
async fn no_citation_path_absent_citations_accepted(pool: sqlx::PgPool) {
    use std::sync::{Arc as StdArc, Mutex};

    let captured_body: StdArc<Mutex<Option<serde_json::Value>>> = StdArc::new(Mutex::new(None));
    let body_cap = StdArc::clone(&captured_body);

    let mut server = mockito::Server::new_async().await;
    let _mock = server
        .mock("POST", "/v1/verify-contradiction")
        .with_status(200)
        .with_header("content-type", "application/json")
        .with_body_from_request(move |req| {
            if let Ok(bytes) = req.body() {
                if let Ok(v) = serde_json::from_slice::<serde_json::Value>(bytes) {
                    *body_cap.lock().unwrap() = Some(v);
                }
            }
            r#"{"verdict":"present","reason":"ok","ticket_action":"not_raised","ticket_id":null}"#
                .into()
        })
        .create_async()
        .await;

    let (caller_cookie, caller_id) = seed_anon_user(&pool).await;
    let conv_id = seed_owned_conversation(&pool, caller_id).await;

    // Absent citations: payload has no citations field at all.
    let payload = format!(r#"{{"claim":"x","conversation_id":"{conv_id}"}}"#);
    let app = router(make_state_with_db_pool(&server.url(), pool));
    let resp = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/v1/report-contradiction")
                .header("content-type", "application/json")
                .header("cookie", format!("{ANON_COOKIE_NAME}={caller_cookie}"))
                .body(Body::from(payload))
                .unwrap(),
        )
        .await
        .unwrap();

    // Gateway must accept absent citations and forward (not 400).
    assert_ne!(resp.status(), StatusCode::BAD_REQUEST);
    assert_eq!(resp.status(), StatusCode::OK);

    // build_workers_body omits citations entirely when the field was absent.
    let forwarded = captured_body.lock().unwrap().clone().unwrap();
    assert!(
        forwarded.get("citations").is_none(),
        "absent citations must be omitted from forwarded body, got: {forwarded}"
    );
}

/// AC-3: more than 50 citations → 400.
#[tokio::test]
async fn ac3_too_many_citations_returns_400() {
    let citations: String = (0..51)
        .map(|i| format!(r#"{{"source_path":"file{i}.md","chunk_ords":[0]}}"#))
        .collect::<Vec<_>>()
        .join(",");
    let payload =
        format!(r#"{{"claim":"x","conversation_id":"{VALID_UUID}","citations":[{citations}]}}"#);
    let app = router(make_state("http://127.0.0.1:1"));
    let resp = post_report(app, &payload).await;
    assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
    let body = body_json(resp).await;
    assert_error_envelope(&body, "invalid_request");
}

/// AC-3: citation with empty `source_path` → 400.
#[tokio::test]
async fn ac3_citation_empty_source_path_returns_400() {
    let payload = format!(
        r#"{{"claim":"x","conversation_id":"{VALID_UUID}","citations":[{{"source_path":"","chunk_ords":[0]}}]}}"#
    );
    let app = router(make_state("http://127.0.0.1:1"));
    let resp = post_report(app, &payload).await;
    assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
    let body = body_json(resp).await;
    assert_error_envelope(&body, "invalid_request");
}

/// AC-3: negative `chunk_ord` → 400.
#[tokio::test]
async fn ac3_negative_chunk_ord_returns_400() {
    let payload = format!(
        r#"{{"claim":"x","conversation_id":"{VALID_UUID}","citations":[{{"source_path":"f.md","chunk_ords":[-1]}}]}}"#
    );
    let app = router(make_state("http://127.0.0.1:1"));
    let resp = post_report(app, &payload).await;
    assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
    let body = body_json(resp).await;
    assert_error_envelope(&body, "invalid_request");
}

/// AC-3: missing required field (no claim) → 400.
#[tokio::test]
async fn ac3_missing_claim_returns_400() {
    let payload = format!(
        r#"{{"conversation_id":"{VALID_UUID}","citations":[{{"source_path":"f.md","chunk_ords":[0]}}]}}"#
    );
    let app = router(make_state("http://127.0.0.1:1"));
    let resp = post_report(app, &payload).await;
    assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
    let body = body_json(resp).await;
    assert_error_envelope(&body, "invalid_request");
}

// ---------------------------------------------------------------------------
// AC-4: upstream errors
// ---------------------------------------------------------------------------

/// AC-4: workers connection refused → 503 `upstream_unavailable`.
///
/// DB-backed: ownership check is fail-closed; seeded owner posts the request
/// so the gateway reaches the workers call and then gets connection-refused.
#[sqlx::test(migrations = "../migrations")]
async fn ac4_connect_failure_returns_503(pool: sqlx::PgPool) {
    let (caller_cookie, caller_id) = seed_anon_user(&pool).await;
    let conv_id = seed_owned_conversation(&pool, caller_id).await;

    let app = router(make_state_with_db_pool("http://127.0.0.1:1", pool));
    let resp = post_report_as_anon(app, caller_cookie, conv_id).await;
    assert_eq!(resp.status(), StatusCode::SERVICE_UNAVAILABLE);
    let body = body_json(resp).await;
    assert_error_envelope(&body, "upstream_unavailable");
}

/// AC-4: workers 500 → 502 `upstream_error`.
///
/// DB-backed: ownership check is fail-closed; seeded owner posts the request.
#[sqlx::test(migrations = "../migrations")]
async fn ac4_workers_500_returns_502(pool: sqlx::PgPool) {
    let mut server = mockito::Server::new_async().await;
    let _mock = server
        .mock("POST", "/v1/verify-contradiction")
        .with_status(500)
        .with_body(r#"{"error":"internal"}"#)
        .create_async()
        .await;

    let (caller_cookie, caller_id) = seed_anon_user(&pool).await;
    let conv_id = seed_owned_conversation(&pool, caller_id).await;

    let app = router(make_state_with_db_pool(&server.url(), pool));
    let resp = post_report_as_anon(app, caller_cookie, conv_id).await;
    assert_eq!(resp.status(), StatusCode::BAD_GATEWAY);
    let body = body_json(resp).await;
    assert_error_envelope(&body, "upstream_error");
}

/// AC-4: workers 400 → 502 (not passthrough).
///
/// DB-backed: ownership check is fail-closed; seeded owner posts the request.
#[sqlx::test(migrations = "../migrations")]
async fn ac4_workers_400_returns_502(pool: sqlx::PgPool) {
    let mut server = mockito::Server::new_async().await;
    let _mock = server
        .mock("POST", "/v1/verify-contradiction")
        .with_status(400)
        .with_body(r#"{"error":"invalid_claim"}"#)
        .create_async()
        .await;

    let (caller_cookie, caller_id) = seed_anon_user(&pool).await;
    let conv_id = seed_owned_conversation(&pool, caller_id).await;

    let app = router(make_state_with_db_pool(&server.url(), pool));
    let resp = post_report_as_anon(app, caller_cookie, conv_id).await;
    assert_eq!(resp.status(), StatusCode::BAD_GATEWAY);
    let body = body_json(resp).await;
    assert_error_envelope(&body, "upstream_error");
}

// ---------------------------------------------------------------------------
// MED-2: fan-out cap — 201 chunk_ords → 400, workers NOT called
// ---------------------------------------------------------------------------

/// AC-3 (fan-out cap): a citation with 201 `chunk_ords` exceeds `MAX_CHUNK_ORDS`=200
/// and must return 400 `invalid_request` without calling workers.
/// Port 1 = loopback refuse — verifies workers is not called.
#[tokio::test]
async fn ac3_too_many_chunk_ords_returns_400() {
    // 201 chunk_ords in one citation — exceeds the MAX_CHUNK_ORDS=200 A04/DoS guard.
    let ords: String = (0..201_u32)
        .map(|i| i.to_string())
        .collect::<Vec<_>>()
        .join(",");
    let payload = format!(
        r#"{{"claim":"x","conversation_id":"{VALID_UUID}","citations":[{{"source_path":"f.md","chunk_ords":[{ords}]}}]}}"#
    );
    let app = router(make_state("http://127.0.0.1:1"));
    let resp = post_report(app, &payload).await;
    assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
    let body = body_json(resp).await;
    assert_error_envelope(&body, "invalid_request");
}

// ---------------------------------------------------------------------------
// #163: force field — forwarded, defaults false, invalid type → 400
// ---------------------------------------------------------------------------

/// #163 AC: force=true is forwarded in the body to workers.
///
/// DB-backed: ownership check is fail-closed; seeded owner posts the request.
#[sqlx::test(migrations = "../migrations")]
async fn force_true_forwarded_to_workers(pool: sqlx::PgPool) {
    use std::sync::{Arc as StdArc, Mutex};

    let captured_body: StdArc<Mutex<Option<serde_json::Value>>> = StdArc::new(Mutex::new(None));
    let body_cap = StdArc::clone(&captured_body);

    let mut server = mockito::Server::new_async().await;
    let _mock = server
        .mock("POST", "/v1/verify-contradiction")
        .with_status(200)
        .with_header("content-type", "application/json")
        .with_body_from_request(move |req| {
            if let Ok(bytes) = req.body() {
                if let Ok(v) = serde_json::from_slice::<serde_json::Value>(bytes) {
                    *body_cap.lock().unwrap() = Some(v);
                }
            }
            r#"{"verdict":"present","reason":"ok","ticket_action":"created","ticket_id":"550e8400-e29b-41d4-a716-446655440001"}"#
                .into()
        })
        .create_async()
        .await;

    let (caller_cookie, caller_id) = seed_anon_user(&pool).await;
    let conv_id = seed_owned_conversation(&pool, caller_id).await;

    let payload = format!(r#"{{"claim":"x","conversation_id":"{conv_id}","force":true}}"#);
    let app = router(make_state_with_db_pool(&server.url(), pool));
    let resp = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/v1/report-contradiction")
                .header("content-type", "application/json")
                .header("cookie", format!("{ANON_COOKIE_NAME}={caller_cookie}"))
                .body(Body::from(payload))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);

    let forwarded = captured_body.lock().unwrap().clone().unwrap();
    assert_eq!(
        forwarded["force"],
        serde_json::json!(true),
        "force=true must be forwarded to workers"
    );
}

/// #173 AC: force=true + workers returns `skipped_error` → body survives gateway passthrough
/// byte-for-byte (`ticket_action` == `skipped_error` is preserved).
///
/// DB-backed: ownership check is fail-closed; seeded owner posts the request.
#[sqlx::test(migrations = "../migrations")]
async fn force_true_skipped_error_passthrough(pool: sqlx::PgPool) {
    let mut server = mockito::Server::new_async().await;
    let _mock = server
        .mock("POST", "/v1/verify-contradiction")
        .with_status(200)
        .with_header("content-type", "application/json")
        .with_body(
            r#"{"verdict":"absent","reason":"write failed","ticket_action":"skipped_error","ticket_id":null,"outcome":"indecisive"}"#,
        )
        .create_async()
        .await;

    let (caller_cookie, caller_id) = seed_anon_user(&pool).await;
    let conv_id = seed_owned_conversation(&pool, caller_id).await;

    let payload = format!(r#"{{"claim":"x","conversation_id":"{conv_id}","force":true}}"#);
    let app = router(make_state_with_db_pool(&server.url(), pool));
    let resp = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/v1/report-contradiction")
                .header("content-type", "application/json")
                .header("cookie", format!("{ANON_COOKIE_NAME}={caller_cookie}"))
                .body(Body::from(payload))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let body = body_json(resp).await;
    assert_eq!(
        body["ticket_action"], "skipped_error",
        "skipped_error must survive gateway passthrough byte-for-byte (#173)"
    );
}

/// #163 AC: force omitted → defaults false, forwarded as false.
///
/// DB-backed: ownership check is fail-closed; seeded owner posts the request.
#[sqlx::test(migrations = "../migrations")]
async fn force_omitted_defaults_false_forwarded(pool: sqlx::PgPool) {
    use std::sync::{Arc as StdArc, Mutex};

    let captured_body: StdArc<Mutex<Option<serde_json::Value>>> = StdArc::new(Mutex::new(None));
    let body_cap = StdArc::clone(&captured_body);

    let mut server = mockito::Server::new_async().await;
    let _mock = server
        .mock("POST", "/v1/verify-contradiction")
        .with_status(200)
        .with_header("content-type", "application/json")
        .with_body_from_request(move |req| {
            if let Ok(bytes) = req.body() {
                if let Ok(v) = serde_json::from_slice::<serde_json::Value>(bytes) {
                    *body_cap.lock().unwrap() = Some(v);
                }
            }
            r#"{"verdict":"present","reason":"ok","ticket_action":"not_raised","ticket_id":null}"#
                .into()
        })
        .create_async()
        .await;

    let (caller_cookie, caller_id) = seed_anon_user(&pool).await;
    let conv_id = seed_owned_conversation(&pool, caller_id).await;

    // Absent force: payload has no force field at all.
    let payload = format!(r#"{{"claim":"x","conversation_id":"{conv_id}"}}"#);
    let app = router(make_state_with_db_pool(&server.url(), pool));
    let resp = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/v1/report-contradiction")
                .header("content-type", "application/json")
                .header("cookie", format!("{ANON_COOKIE_NAME}={caller_cookie}"))
                .body(Body::from(payload))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);

    let forwarded = captured_body.lock().unwrap().clone().unwrap();
    assert_eq!(
        forwarded["force"],
        serde_json::json!(false),
        "omitted force must default to false in forwarded body"
    );
}

/// #163 AC: force is not a bool (e.g. a string) → 400 `invalid_request`.
#[tokio::test]
async fn force_non_bool_returns_400() {
    let payload = format!(r#"{{"claim":"x","conversation_id":"{VALID_UUID}","force":"yes"}}"#);
    let app = router(make_state("http://127.0.0.1:1"));
    let resp = post_report(app, &payload).await;
    assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
    let body = body_json(resp).await;
    assert_error_envelope(&body, "invalid_request");
}

// ---------------------------------------------------------------------------
// LOW: route body-limit → uniform 400 envelope (not raw 413)
// ---------------------------------------------------------------------------

/// A04: body > 1 MiB → `RequestBodyLimitLayer` fires; `handle_body_limit_error`
/// rewrites 413 → 400 `invalid_request` with uniform error envelope.
/// Workers must NOT be called (port 1 = loopback refuse).
#[tokio::test]
async fn low_body_too_large_returns_400_envelope() {
    let app = router(make_state("http://127.0.0.1:1"));
    let big_body = vec![b'x'; 1_048_577]; // 1 MiB + 1
    let resp = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/v1/report-contradiction")
                .header("content-type", "application/json")
                .body(Body::from(big_body))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
    let body = body_json(resp).await;
    // handle_body_limit_error rewrites 413 → 400 with `invalid_request` code.
    assert_eq!(body["error"], "invalid_request");
}

// ---------------------------------------------------------------------------
// LOW: upstream timeout → 504 upstream_timeout
// ---------------------------------------------------------------------------

/// A04: workers never responds within the configured timeout → 504 `upstream_timeout`.
/// Uses a TCP listener that accepts but never sends, with a 500ms request timeout.
///
/// DB-backed: ownership check is fail-closed; seeded owner posts the request so
/// the gateway reaches the workers call and then hits the timeout.
#[sqlx::test(migrations = "../migrations")]
async fn low_upstream_timeout_returns_504(pool: sqlx::PgPool) {
    use tokio::net::TcpListener;

    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();

    // Accept in background — never respond, hold the socket open.
    tokio::spawn(async move {
        if let Ok((_socket, _)) = listener.accept().await {
            tokio::time::sleep(std::time::Duration::from_mins(1)).await;
        }
    });

    let (caller_cookie, caller_id) = seed_anon_user(&pool).await;
    let conv_id = seed_owned_conversation(&pool, caller_id).await;

    let workers_url = format!("http://{addr}");
    let app = router(make_state_with_short_timeout_and_pool(&workers_url, pool));
    let resp = post_report_as_anon(app, caller_cookie, conv_id).await;

    assert_eq!(resp.status(), StatusCode::GATEWAY_TIMEOUT);
    let body = body_json(resp).await;
    assert_error_envelope(&body, "upstream_timeout");
}

// ---------------------------------------------------------------------------
// LOW: overhead header present (OPS-001a parity with chat_router)
// ---------------------------------------------------------------------------

/// OPS-001 AC-4: `X-Gateway-Overhead-Ms` is present on 200 responses from
/// `POST /v1/report-contradiction` (`overhead_header` middleware parity with chat).
///
/// DB-backed: ownership check is fail-closed; seeded owner posts the request.
#[sqlx::test(migrations = "../migrations")]
async fn low_overhead_header_present_on_200(pool: sqlx::PgPool) {
    let mut server = mockito::Server::new_async().await;
    let _mock = server
        .mock("POST", "/v1/verify-contradiction")
        .with_status(200)
        .with_header("content-type", "application/json")
        .with_body(
            r#"{"verdict":"present","reason":"Fait confirmé.","ticket_action":"not_raised","ticket_id":null}"#,
        )
        .create_async()
        .await;

    let (caller_cookie, caller_id) = seed_anon_user(&pool).await;
    let conv_id = seed_owned_conversation(&pool, caller_id).await;

    let app = router(make_state_with_db_pool(&server.url(), pool));
    let resp = post_report_as_anon(app, caller_cookie, conv_id).await;

    assert_eq!(resp.status(), StatusCode::OK);
    assert!(
        resp.headers().contains_key("x-gateway-overhead-ms"),
        "X-Gateway-Overhead-Ms must be present on report-contradiction 200"
    );
}

/// OPS-001 AC-4: `X-Gateway-Overhead-Ms` is present on 400 (validation failure,
/// workers not called) — mirrors the chat suite ac4e test.
#[tokio::test]
async fn low_overhead_header_present_on_400() {
    let app = router(make_state("http://127.0.0.1:1"));
    let payload = format!(
        r#"{{"claim":"","conversation_id":"{VALID_UUID}","citations":[{{"source_path":"f.md","chunk_ords":[0]}}]}}"#
    );
    let resp = post_report(app, &payload).await;

    assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
    assert!(
        resp.headers().contains_key("x-gateway-overhead-ms"),
        "X-Gateway-Overhead-Ms must be present even on 400 (no workers call)"
    );
}

// ---------------------------------------------------------------------------
// A01/IDOR: DB-backed ownership tests (#163 review fix)
// ---------------------------------------------------------------------------

fn make_config_db() -> archiviste_gateway::config::Config {
    archiviste_gateway::config::Config {
        bind_addr: "127.0.0.1:0".to_string(),
        workers_url: "http://127.0.0.1:1".to_string(),
        database_url: "postgres://test".to_string(),
        jwt_ed25519_public_key_pem: test_public_key_pem().to_string(),
        jwt_ed25519_private_key_pem: SecretString::from(test_private_key_pem().to_string()),
        jwt_kid: TEST_KEY_ID.to_string(),
        version: "0.1.0".to_string(),
        connect_timeout_ms: 500,
        request_timeout_ms: 5_000,
        chat_request_timeout_ms: 90_000,
        gcs_signing_sa_email: "test-sa@project.iam.gserviceaccount.com".to_string(),
        gcs_bucket: "archiviste-conversations".to_string(),
        cloud_run_service_url: "http://127.0.0.1:1".to_string(),
        cost_tariffs: Some(archiviste_gateway::config::CostTariffs::default()),
    }
}

fn make_state_with_db_pool(workers_url: &str, pool: sqlx::PgPool) -> Arc<AppState> {
    let mut config = make_config_db();
    config.workers_url = workers_url.to_string();
    // Stub workers ID-token (no metadata-server call) + attached pool: the forwarding
    // path needs a valid bearer, the A01 ownership check needs the pool. `new_with_pool`
    // alone wires the REAL metadata provider, whose fetch fails in CI → spurious 503.
    let id_token_provider = Arc::new(
        archiviste_gateway::auth_metadata::IdTokenProvider::new_stub_always_valid().unwrap(),
    );
    let mut state = AppState::new_with_id_token_provider(config, id_token_provider).unwrap();
    state.db_pool = Some(pool);
    Arc::new(state)
}

/// Seed a bare anonymous user row and return their `user_id` (derived via `UUIDv5`).
async fn seed_anon_user(pool: &sqlx::PgPool) -> (Uuid, Uuid) {
    let cookie_uuid = Uuid::new_v4();
    let user_id = cookie_uuid_to_user_id(&cookie_uuid);
    sqlx::query("INSERT INTO users (id, tier) VALUES ($1, 'anonymous')")
        .bind(user_id)
        .execute(pool)
        .await
        .unwrap();
    (cookie_uuid, user_id)
}

/// Seed a conversation owned by `owner_id` and return its UUID.
async fn seed_owned_conversation(pool: &sqlx::PgPool, owner_id: Uuid) -> Uuid {
    let conv_id = Uuid::new_v4();
    let gcs_uri = format!("gs://archiviste-conversations/conv/{conv_id}.md");
    sqlx::query(
        "INSERT INTO conversations (id, user_id, gcs_uri, message_count) \
         VALUES ($1, $2, $3, 1)",
    )
    .bind(conv_id)
    .bind(owner_id)
    .bind(&gcs_uri)
    .execute(pool)
    .await
    .unwrap();
    conv_id
}

/// Post a report as a specific anonymous caller identified by `cookie_uuid`.
async fn post_report_as_anon(
    app: axum::Router,
    cookie_uuid: Uuid,
    conversation_id: Uuid,
) -> axum::response::Response {
    let payload = format!(
        r#"{{"claim":"Nocilia was founded in year 0.","conversation_id":"{conversation_id}"}}"#
    );
    app.oneshot(
        Request::builder()
            .method("POST")
            .uri("/v1/report-contradiction")
            .header("content-type", "application/json")
            .header("cookie", format!("{ANON_COOKIE_NAME}={cookie_uuid}"))
            .body(Body::from(payload))
            .unwrap(),
    )
    .await
    .unwrap()
}

/// A01 IDOR: signal against a `conversation_id` owned by a *different* caller → 404,
/// workers NOT called (port 1 = loopback refused; any forwarding attempt would error
/// differently — but the check fires before the workers call, so the response is 404).
///
/// AC reference: security.md A01 — IDOR check in the handler, uniform 404.
#[sqlx::test(migrations = "../migrations")]
async fn idor_foreign_conversation_returns_404_workers_not_called(pool: sqlx::PgPool) {
    // Seed the real owner and their conversation.
    let (_real_owner_cookie, real_owner_id) = seed_anon_user(&pool).await;
    let conv_id = seed_owned_conversation(&pool, real_owner_id).await;

    // A different caller (different cookie → different user_id).
    let (attacker_cookie, _attacker_id) = seed_anon_user(&pool).await;

    // Port 1 = loopback refuse: if workers were ever called the response would be 503,
    // not 404 — this makes the "workers not called" assertion implicit in the status check.
    let app = router(make_state_with_db_pool("http://127.0.0.1:1", pool));
    let resp = post_report_as_anon(app, attacker_cookie, conv_id).await;

    // A01: foreign conversation → uniform 404 (not 403, not 502).
    assert_eq!(
        resp.status(),
        StatusCode::NOT_FOUND,
        "non-owner must get 404, not be forwarded to workers"
    );
    let body = body_json(resp).await;
    assert_eq!(body["error"], "conversation_not_found");
}

/// A01 IDOR: signal against a `conversation_id` that does not exist at all → 404,
/// workers NOT called (mirrors the non-existent case in HIST-001).
///
/// AC reference: security.md A01 — absent conversation treated identically to foreign.
#[sqlx::test(migrations = "../migrations")]
async fn idor_nonexistent_conversation_returns_404(pool: sqlx::PgPool) {
    let (caller_cookie, _caller_id) = seed_anon_user(&pool).await;
    let nonexistent_id = Uuid::new_v4();

    let app = router(make_state_with_db_pool("http://127.0.0.1:1", pool));
    let resp = post_report_as_anon(app, caller_cookie, nonexistent_id).await;

    assert_eq!(
        resp.status(),
        StatusCode::NOT_FOUND,
        "absent conversation must get 404"
    );
    let body = body_json(resp).await;
    assert_eq!(body["error"], "conversation_not_found");
}

/// A01 IDOR (happy path): signal against an owned conversation → forwarded to workers.
///
/// Without a real workers endpoint the gateway gets a connection-refused (503), but
/// the ownership check passed (no 404 returned by the gateway itself).
#[sqlx::test(migrations = "../migrations")]
async fn idor_owned_conversation_is_forwarded(pool: sqlx::PgPool) {
    let (caller_cookie, caller_id) = seed_anon_user(&pool).await;
    let conv_id = seed_owned_conversation(&pool, caller_id).await;

    // Port 1 = loopback refuse → 503 after ownership passes, proving we got past the check.
    let app = router(make_state_with_db_pool("http://127.0.0.1:1", pool));
    let resp = post_report_as_anon(app, caller_cookie, conv_id).await;

    // 503 means ownership check passed and gateway attempted the workers call.
    assert_eq!(
        resp.status(),
        StatusCode::SERVICE_UNAVAILABLE,
        "owned conversation must be forwarded (503 = workers refused, not 404)"
    );
}
