//! Integration + contract tests for OBS-004.
//!
//! Covers AC-1..AC-7, AC-12, AC-13, AC-5 contract.
//! AC-8/AC-9/AC-10/AC-11/AC-15 are manual author checks.

#![allow(clippy::unwrap_used)]
#![allow(clippy::expect_used)]

mod common;
use common::jwt_helpers::make_app_state;

use archiviste_gateway::{router, state::AppState};
use axum::{
    body::Body,
    http::{Request, StatusCode},
};
use http_body_util::BodyExt;
use std::sync::Arc;
use tower::ServiceExt;
use uuid::Uuid;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

fn make_no_pool_state() -> Arc<AppState> {
    // db_pool = None → UpstreamUnavailable (AC-7).
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

async fn body_bytes(resp: axum::response::Response) -> Vec<u8> {
    resp.into_body()
        .collect()
        .await
        .unwrap()
        .to_bytes()
        .to_vec()
}

async fn body_json(resp: axum::response::Response) -> serde_json::Value {
    let bytes = resp.into_body().collect().await.unwrap().to_bytes();
    serde_json::from_slice(&bytes).expect("response body must be valid JSON")
}

const CSP_VALUE: &str = "default-src 'self'; script-src 'self'; style-src 'self'; \
    img-src 'self' data:; object-src 'none'; frame-ancestors 'none'; \
    base-uri 'none'; form-action 'self'";
const HSTS_VALUE: &str = "max-age=31536000; includeSubDomains; preload";

fn assert_security_headers(resp: &axum::response::Response) {
    let h = resp.headers();
    assert_eq!(
        h.get("strict-transport-security")
            .unwrap()
            .to_str()
            .unwrap(),
        HSTS_VALUE
    );
    assert_eq!(
        h.get("content-security-policy").unwrap().to_str().unwrap(),
        CSP_VALUE
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
// DB helpers (used by #[sqlx::test] cases)
// ---------------------------------------------------------------------------

#[cfg(test)]
fn make_db_config() -> archiviste_gateway::config::Config {
    use common::jwt_helpers::{test_private_key_pem, test_public_key_pem, TEST_KEY_ID};
    archiviste_gateway::config::Config {
        bind_addr: "127.0.0.1:0".to_string(),
        workers_url: "http://127.0.0.1:1".to_string(),
        database_url: "postgres://test".to_string(),
        jwt_ed25519_public_key_pem: test_public_key_pem().to_string(),
        jwt_ed25519_private_key_pem: secrecy::SecretString::from(
            test_private_key_pem().to_string(),
        ),
        jwt_kid: TEST_KEY_ID.to_string(),
        version: "0.1.0".to_string(),
        connect_timeout_ms: 500,
        request_timeout_ms: 35_000,
        chat_request_timeout_ms: 90_000,
        gcs_signing_sa_email: "test-sa@project.iam.gserviceaccount.com".to_string(),
        gcs_bucket: "archiviste-conversations".to_string(),
        cloud_run_service_url: "http://127.0.0.1:1".to_string(),
        cost_tariffs: Some(archiviste_gateway::config::CostTariffs::default()),
    }
}

#[cfg(test)]
#[allow(clippy::too_many_arguments)]
async fn insert_live_run(
    pool: &sqlx::PgPool,
    faithfulness: &str,
    answer_relevancy: &str,
    context_precision: &str,
    context_recall: &str,
    golden_set_version: &str,
    finished_at_offset_secs: i64,
) {
    sqlx::query(
        "INSERT INTO eval_runs \
         (id, git_sha, runner_mode, golden_set_version, \
          faithfulness, answer_relevancy, context_precision, context_recall, \
          entries_total, entries_ok, entries_errors, started_at, finished_at) \
         VALUES ($1, 'abc123', 'live', $2, \
         $3::numeric, $4::numeric, $5::numeric, $6::numeric, 10, 10, 0, \
         NOW() - INTERVAL '1 hour', \
         '2026-01-01 00:00:00+00'::timestamptz + ($7 || ' seconds')::interval)",
    )
    .bind(Uuid::new_v4())
    .bind(golden_set_version)
    .bind(faithfulness)
    .bind(answer_relevancy)
    .bind(context_precision)
    .bind(context_recall)
    .bind(finished_at_offset_secs.to_string())
    .execute(pool)
    .await
    .unwrap();
}

// ---------------------------------------------------------------------------
// Serialization regression: BigDecimal must emit JSON number, not string
// (AC-1 / AC-3). This runs under plain `cargo test` without Postgres.
// The bug: `bigdecimal` default `Serialize` (feature "serde") calls
// `serializer.collect_str(&self)` → emits `"0.9234"` (a string).
// Fix: feature "serde-json" + `#[serde(with = "bigdecimal::serde::json_num")]`
// → `serde_json::Number::from_str("0.9234").serialize(...)` → bare number.
// ---------------------------------------------------------------------------

/// AC-1 / AC-3 (regression, no DB): `QualityMetrics` serializes metrics as JSON
/// numbers, not strings. `v["faithfulness"].is_number()` must be true.
#[test]
fn serialization_bigdecimal_emits_json_number_not_string() {
    use archiviste_gateway::handlers::quality::QualityMetrics;
    use bigdecimal::BigDecimal;
    use chrono::Utc;
    use std::str::FromStr;

    // AC-3: NUMERIC(5,4) value 0.9234 must survive round-trip as a JSON number.
    let metrics = QualityMetrics {
        faithfulness: BigDecimal::from_str("0.9234").expect("valid decimal"),
        answer_relevancy: BigDecimal::from_str("0.8765").expect("valid decimal"),
        context_precision: BigDecimal::from_str("0.7891").expect("valid decimal"),
        context_recall: BigDecimal::from_str("0.6543").expect("valid decimal"),
        golden_set_version: "v1.0".to_string(),
        finished_at: Utc::now(),
    };

    let v = serde_json::to_value(&metrics).expect("QualityMetrics must serialize to JSON");

    // AC-3: must be JSON number, not string.
    assert!(
        v["faithfulness"].is_number(),
        "faithfulness must be a JSON number (not string): got {:?}",
        v["faithfulness"]
    );
    assert!(
        v["answer_relevancy"].is_number(),
        "answer_relevancy must be JSON number"
    );
    assert!(
        v["context_precision"].is_number(),
        "context_precision must be JSON number"
    );
    assert!(
        v["context_recall"].is_number(),
        "context_recall must be JSON number"
    );

    // AC-3: numeric equality with stored value.
    assert_eq!(
        v["faithfulness"],
        serde_json::json!(0.9234_f64),
        "faithfulness must equal 0.9234 as JSON number"
    );

    // AC-3: serialized string must contain 0.9234 unquoted (not \"0.9234\").
    let serialized = serde_json::to_string(&metrics).expect("must serialize to string");
    assert!(
        serialized.contains("0.9234"),
        "serialized body must contain 0.9234 unquoted"
    );
    assert!(
        !serialized.contains("\"0.9234\""),
        "serialized body must NOT contain quoted \\\"0.9234\\\" (string bug)"
    );
}

// ---------------------------------------------------------------------------
// AC-4 : empty eval_runs → 200 {"status":"no_data"}
// ---------------------------------------------------------------------------

/// AC-4: 0 live rows → 200 `{"status":"no_data"}` (not 404, not 503).
#[sqlx::test(migrations = "../migrations")]
async fn ac4_empty_eval_runs_returns_no_data(pool: sqlx::PgPool) {
    let state = Arc::new(AppState::new_with_pool(make_db_config(), pool).unwrap());
    let resp = get_anon(router(state), "/v1/quality").await;

    assert_eq!(resp.status(), StatusCode::OK);
    let json = body_json(resp).await;
    assert_eq!(json["status"], "no_data");
    assert_eq!(json.as_object().unwrap().len(), 1, "exactly 1 key: status");
    assert!(json["faithfulness"].is_null());
    assert!(json["finished_at"].is_null());
}

// ---------------------------------------------------------------------------
// AC-1 / AC-3 / AC-6 : 6 keys exact, numeric equality, RFC3339
// ---------------------------------------------------------------------------

/// AC-1: 200, 6 keys exact, no extras.
/// AC-3: metrics are JSON numbers equal to NUMERIC(5,4), no rounding.
/// AC-6: `finished_at` parses as RFC3339.
#[sqlx::test(migrations = "../migrations")]
async fn ac1_ac3_ac6_one_live_row_six_keys(pool: sqlx::PgPool) {
    insert_live_run(&pool, "0.9234", "0.8765", "0.7891", "0.6543", "v1.0", 100).await;

    let state = Arc::new(AppState::new_with_pool(make_db_config(), pool).unwrap());
    let resp = get_anon(router(state), "/v1/quality").await;

    assert_eq!(resp.status(), StatusCode::OK);

    // AC-1 charset: content-type must be application/json; charset=utf-8.
    let ct = resp
        .headers()
        .get("content-type")
        .expect("content-type missing on 200")
        .to_str()
        .unwrap();
    assert_eq!(
        ct, "application/json; charset=utf-8",
        "AC-1: content-type must be application/json; charset=utf-8, got: {ct}"
    );
    // Exactly one content-type header (no duplicate).
    assert_eq!(
        resp.headers()
            .get_all(axum::http::header::CONTENT_TYPE)
            .iter()
            .count(),
        1,
        "AC-1: must have exactly one content-type header"
    );

    let json = body_json(resp).await;

    // AC-1: exactly 6 keys.
    assert_eq!(json.as_object().unwrap().len(), 6);
    assert!(json["id"].is_null(), "id must not appear (AC-1 non-goals)");
    assert!(json["git_sha"].is_null(), "git_sha must not appear");
    assert!(
        json["status"].is_null(),
        "status must not appear in metrics body"
    );

    // AC-3: strict numeric equality.
    assert_eq!(json["faithfulness"], serde_json::json!(0.9234_f64));
    assert_eq!(json["answer_relevancy"], serde_json::json!(0.8765_f64));
    assert_eq!(json["context_precision"], serde_json::json!(0.7891_f64));
    assert_eq!(json["context_recall"], serde_json::json!(0.6543_f64));
    // AC-3: must be a JSON number not a string.
    assert!(
        json["faithfulness"].is_number(),
        "faithfulness must be JSON number"
    );

    // AC-6: finished_at parses as RFC3339.
    let ts = json["finished_at"]
        .as_str()
        .expect("finished_at must be a string");
    chrono::DateTime::parse_from_rfc3339(ts).expect("finished_at must parse as RFC3339");
    assert_eq!(json["golden_set_version"], "v1.0");
}

// ---------------------------------------------------------------------------
// AC-2 : two live rows → latest served
// ---------------------------------------------------------------------------

/// AC-2: two rows t1 < t2; t2 values are served (maximise `finished_at`).
#[sqlx::test(migrations = "../migrations")]
async fn ac2_two_rows_latest_served(pool: sqlx::PgPool) {
    insert_live_run(
        &pool, "0.1000", "0.1000", "0.1000", "0.1000", "v1.0-old", 100,
    )
    .await;
    insert_live_run(
        &pool, "0.9999", "0.9999", "0.9999", "0.9999", "v2.0-new", 200,
    )
    .await;

    let state = Arc::new(AppState::new_with_pool(make_db_config(), pool).unwrap());
    let resp = get_anon(router(state), "/v1/quality").await;

    assert_eq!(resp.status(), StatusCode::OK);
    let json = body_json(resp).await;
    assert_eq!(json["faithfulness"], serde_json::json!(0.9999_f64));
    assert_eq!(json["golden_set_version"], "v2.0-new");
}

// ---------------------------------------------------------------------------
// AC-7 : no pool → 503, sanitized body
// ---------------------------------------------------------------------------

/// AC-7: `pool=None` → 503 `{"error":"upstream_unavailable","request_id":"<uuid>"}`.
/// Negative: no `eval_runs`/`SELECT`/`postgres` detail in body (security.md §A05).
#[tokio::test]
async fn ac7_no_pool_returns_503_sanitized() {
    let resp = get_anon(router(make_no_pool_state()), "/v1/quality").await;

    assert_eq!(resp.status(), StatusCode::SERVICE_UNAVAILABLE);

    let bytes = body_bytes(resp).await;
    let body_str = String::from_utf8_lossy(&bytes);
    let json: serde_json::Value =
        serde_json::from_slice(&bytes).expect("503 body must be valid JSON");

    assert_eq!(json["error"], "upstream_unavailable");
    let rid = json["request_id"]
        .as_str()
        .expect("request_id must be string");
    assert_eq!(rid.len(), 36, "request_id must be 36-char UUID");
    assert_eq!(json.as_object().unwrap().len(), 2, "exactly 2 keys");

    let lower = body_str.to_lowercase();
    assert!(!lower.contains("eval_runs"));
    assert!(!lower.contains("select"));
    assert!(!lower.contains("postgres"));
    assert!(!lower.contains("panic"));
}

// ---------------------------------------------------------------------------
// AC-12 : 5 security headers on /v1/quality 503 (no-pool path)
// ---------------------------------------------------------------------------

/// AC-12: 5 headers present byte-for-byte on /v1/quality even on 503 path.
#[tokio::test]
async fn ac12_quality_503_has_security_headers() {
    let resp = get_anon(router(make_no_pool_state()), "/v1/quality").await;
    assert_eq!(resp.status(), StatusCode::SERVICE_UNAVAILABLE);
    assert_security_headers(&resp);
}

// ---------------------------------------------------------------------------
// AC-5 contract : quality.rs has no auth guard; lib.rs mounts in public_api
// ---------------------------------------------------------------------------

/// AC-5 (contract): `quality.rs` has no `RequireAuthor`/`RequireMember`/`author_only` guard.
#[test]
fn ac5_contract_no_auth_guard_in_quality_handler() {
    let src = include_str!("../src/handlers/quality.rs");
    assert!(
        !src.contains("RequireAuthor"),
        "quality.rs must not contain RequireAuthor"
    );
    assert!(
        !src.contains("RequireMember"),
        "quality.rs must not contain RequireMember"
    );
    assert!(
        !src.contains("author_only") && !src.contains("member_only"),
        "quality.rs must not contain auth guard"
    );
}

/// AC-5 (contract): `lib.rs` mounts /v1/quality in the `public_api` router.
#[test]
fn ac5_contract_lib_mounts_quality_in_public_api() {
    let src = include_str!("../src/lib.rs");
    assert!(src.contains("public_api") && src.contains("/v1/quality"));
}

// ---------------------------------------------------------------------------
// AC-13 contract : observability.html no inline + section id="quality-widget"
// ---------------------------------------------------------------------------

/// AC-13 (contract): observability.html has no inline script/style/on* and has quality-widget.
#[test]
fn ac13_no_inline_and_quality_widget_present() {
    let html = std::fs::read_to_string("static/observability.html")
        .expect("static/observability.html not found");

    assert!(!html.contains("<script>") && !html.contains("<script\n"));
    assert!(!html.contains("<style"));
    assert!(!html.contains("style=\"") && !html.contains("style='"));

    let lower = html.to_lowercase();
    let has_on = lower.contains(" onclick=")
        || lower.contains(" onload=")
        || lower.contains(" onsubmit=")
        || lower.contains(" oninput=")
        || lower.contains(" onchange=");
    assert!(
        !has_on,
        "observability.html must not have inline on*= handlers"
    );

    assert!(
        html.contains(r#"<section id="quality-widget""#),
        r#"observability.html must contain <section id="quality-widget""#
    );
}
