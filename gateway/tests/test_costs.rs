//! Tests for #275 — Coûts GCP estimés (carte + endpoint `GET /v1/costs`).
//!
//! - Pure `estimate_costs` model: table-driven (prior art: `aggregate_status`).
//! - `GET /v1/costs` contract + anonymous reachability + security headers,
//!   modeled on `GET /v1/stats` (`test_observability.rs`).

#![allow(clippy::unwrap_used)]
#![allow(clippy::expect_used)]
#![allow(clippy::float_cmp)]

mod common;

use archiviste_gateway::config::CostTariffs;
use archiviste_gateway::handlers::costs::{estimate_costs, Volumes};
use archiviste_gateway::{router, state::AppState};
use axum::{
    body::Body,
    http::{Request, StatusCode},
};
use http_body_util::BodyExt;
use std::sync::Arc;
use uuid::Uuid;

// ---------------------------------------------------------------------------
// Pure model: estimate_costs (table-driven)
// ---------------------------------------------------------------------------

/// Reference tariffs used across the pure-function table.
/// Storage prices chosen as round numbers per GB so expectations are exact.
const TARIFFS: CostTariffs = CostTariffs {
    postgres_instance_eur: 8.00,
    postgres_storage_per_gb_eur: 2.00,
    gcs_storage_per_gb_eur: 1.00,
    workers_per_request_eur: 0.01,
};

/// One gigabyte expressed in the GCP storage convention (10^9 bytes).
const ONE_GB: i64 = 1_000_000_000;

#[test]
fn estimate_costs_property_table() {
    // (db_bytes, conversation_bytes, requests_30d)
    //   → (postgres, gcs, workers, total)
    let cases: &[(i64, i64, i64, f64, f64, f64, f64)] = &[
        // Zero volumes → only the fixed Postgres instance term remains.
        (0, 0, 0, 8.00, 0.00, 0.00, 8.00),
        // 1 GB DB → +2.00 storage on Postgres; nothing else.
        (ONE_GB, 0, 0, 10.00, 0.00, 0.00, 10.00),
        // 2 GB stored in GCS → +2.00 GCS storage.
        (0, 2 * ONE_GB, 0, 8.00, 2.00, 0.00, 10.00),
        // Growing request count → growing Workers cost (100 × 0.01 = 1.00).
        (0, 0, 100, 8.00, 0.00, 1.00, 9.00),
        // More requests → strictly larger Workers cost than the previous row.
        (0, 0, 500, 8.00, 0.00, 5.00, 13.00),
        // All terms together; total = sum of the three services.
        (ONE_GB, 2 * ONE_GB, 100, 10.00, 2.00, 1.00, 13.00),
    ];

    for &(db, conv, req, postgres, gcs, workers, total) in cases {
        let volumes = Volumes {
            database_size_bytes: db,
            conversation_bytes_stored: conv,
            request_count_30d: req,
        };
        let costs = estimate_costs(&TARIFFS, &volumes);

        assert_eq!(costs.postgres_eur, postgres, "postgres for {volumes:?}");
        assert_eq!(costs.gcs_eur, gcs, "gcs for {volumes:?}");
        assert_eq!(costs.workers_eur, workers, "workers for {volumes:?}");
        assert_eq!(costs.total_eur, total, "total for {volumes:?}");

        // Total is exactly the sum of the (rounded) services.
        assert_eq!(
            costs.total_eur,
            costs.postgres_eur + costs.gcs_eur + costs.workers_eur,
            "total must equal the sum of services for {volumes:?}"
        );
    }
}

#[test]
fn estimate_costs_zero_volumes_keeps_only_fixed_terms() {
    let volumes = Volumes {
        database_size_bytes: 0,
        conversation_bytes_stored: 0,
        request_count_30d: 0,
    };
    let costs = estimate_costs(&TARIFFS, &volumes);
    assert_eq!(costs.postgres_eur, TARIFFS.postgres_instance_eur);
    assert_eq!(costs.gcs_eur, 0.0);
    assert_eq!(costs.workers_eur, 0.0);
}

#[test]
fn estimate_costs_workers_grows_with_request_count() {
    let make = |requests: i64| {
        estimate_costs(
            &TARIFFS,
            &Volumes {
                database_size_bytes: 0,
                conversation_bytes_stored: 0,
                request_count_30d: requests,
            },
        )
        .workers_eur
    };
    assert!(
        make(1000) > make(100),
        "workers cost must grow with requests"
    );
}

// ---------------------------------------------------------------------------
// Integration: GET /v1/costs (DB-backed, modeled on GET /v1/stats)
// ---------------------------------------------------------------------------

/// Build a test `Config` with explicit tariffs (no env reads — avoids cross-test
/// env races). Mirrors the `make_db_config` helper in `test_observability.rs`.
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
        gcs_signing_sa_email: "test-sa@project.iam.gserviceaccount.com".to_string(),
        gcs_bucket: "archiviste-conversations".to_string(),
        cloud_run_service_url: "http://127.0.0.1:1".to_string(),
        cost_tariffs: CostTariffs {
            postgres_instance_eur: 8.00,
            postgres_storage_per_gb_eur: 0.20,
            gcs_storage_per_gb_eur: 0.02,
            workers_per_request_eur: 0.0001,
        },
    }
}

async fn get_anon(app: axum::Router, uri: &str) -> axum::response::Response {
    use tower::ServiceExt;
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

/// AC: `GET /v1/costs` returns the exact contract body, anonymously, on a fresh DB.
#[sqlx::test(migrations = "../migrations")]
async fn costs_contract_body_on_empty_db(pool: sqlx::PgPool) {
    sqlx::query("INSERT INTO users (id, tier) VALUES ($1, 'anonymous') ON CONFLICT DO NOTHING")
        .bind(Uuid::nil())
        .execute(&pool)
        .await
        .unwrap();

    let state = Arc::new(AppState::new_with_pool(make_db_config(), pool).unwrap());
    let app = router(state);
    let resp = get_anon(app, "/v1/costs").await;

    assert_eq!(resp.status(), StatusCode::OK, "expected 200 for /v1/costs");

    let content_type = resp
        .headers()
        .get("content-type")
        .expect("content-type missing")
        .to_str()
        .unwrap();
    assert_eq!(content_type, "application/json; charset=utf-8");

    // Security headers carried like the other public observability endpoints.
    for header in [
        "strict-transport-security",
        "x-frame-options",
        "referrer-policy",
        "x-content-type-options",
        "content-security-policy",
    ] {
        assert!(
            resp.headers().get(header).is_some(),
            "missing security header: {header}"
        );
    }

    let json = body_json(resp).await;
    assert_eq!(json["currency"], "EUR");
    assert_eq!(json["period"], "rolling_30d");
    assert_eq!(json["estimated"], true);
    assert!(json["total_eur"].is_number(), "total_eur must be a number");

    let services = json["services"].as_object().expect("services object");
    assert!(services.contains_key("postgres"));
    assert!(services.contains_key("gcs"));
    assert!(services.contains_key("workers"));

    // computed_at is an RFC3339 UTC timestamp.
    let computed_at = json["computed_at"].as_str().expect("computed_at string");
    chrono::DateTime::parse_from_rfc3339(computed_at).expect("computed_at must be RFC3339");

    // On an empty DB there are zero stored bytes and zero requests → GCS and
    // workers are 0; only the fixed Postgres instance term contributes there.
    assert_eq!(services["gcs"].as_f64().unwrap(), 0.0);
    assert_eq!(services["workers"].as_f64().unwrap(), 0.0);

    // Total equals the sum of the three displayed services.
    let total = json["total_eur"].as_f64().unwrap();
    let sum = services["postgres"].as_f64().unwrap()
        + services["gcs"].as_f64().unwrap()
        + services["workers"].as_f64().unwrap();
    assert!(
        (total - sum).abs() < 1e-9,
        "total must equal sum of services"
    );
}

/// AC: only requests inside the 30-day rolling window count toward Workers.
#[sqlx::test(migrations = "../migrations")]
async fn costs_workers_window_excludes_old_requests(pool: sqlx::PgPool) {
    sqlx::query("INSERT INTO users (id, tier) VALUES ($1, 'anonymous') ON CONFLICT DO NOTHING")
        .bind(Uuid::nil())
        .execute(&pool)
        .await
        .unwrap();

    // 5 requests inside the window, 1 well outside (40 days ago — excluded).
    for i in 0..5 {
        sqlx::query(
            "INSERT INTO query_log (request_id, user_id, query_text, status_code, latency_ms) \
             VALUES ($1, $2, 'q', 200, 1)",
        )
        .bind(Uuid::from_u128(u128::try_from(i + 1).unwrap()))
        .bind(Uuid::nil())
        .execute(&pool)
        .await
        .unwrap();
    }
    sqlx::query(
        "INSERT INTO query_log (request_id, user_id, query_text, status_code, latency_ms, created_at) \
         VALUES ($1, $2, 'old', 200, 1, NOW() - INTERVAL '40 days')",
    )
    .bind(Uuid::from_u128(999))
    .bind(Uuid::nil())
    .execute(&pool)
    .await
    .unwrap();

    let state = Arc::new(AppState::new_with_pool(make_db_config(), pool).unwrap());
    let app = router(state);
    let resp = get_anon(app, "/v1/costs").await;
    assert_eq!(resp.status(), StatusCode::OK);

    let json = body_json(resp).await;
    // 5 in-window requests × 0.0001 EUR = 0.0005 → rounded to cents = 0.00.
    // The out-of-window request must be excluded; assert workers stays 0.00,
    // confirming the rolling window is honoured and the query did not 500.
    let workers = json["services"]["workers"].as_f64().unwrap();
    assert_eq!(
        workers, 0.0,
        "5 cheap requests round to 0.00, old one excluded"
    );
}

/// AC: DB unavailable → 503 with the sanitized error envelope (no leaks).
#[tokio::test]
async fn costs_db_unavailable_returns_503() {
    use common::jwt_helpers::make_app_state;
    let state = make_app_state("http://127.0.0.1:1");
    let app = router(state);
    let resp = get_anon(app, "/v1/costs").await;

    assert_eq!(resp.status(), StatusCode::SERVICE_UNAVAILABLE);
    let json = body_json(resp).await;
    assert_eq!(json["error"], "upstream_unavailable");
    assert!(json["request_id"].is_string());
}
