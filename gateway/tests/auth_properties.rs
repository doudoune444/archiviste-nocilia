//! Property test for SEC-001 PR-a.
//!
//! INV-6: Any request without a valid JWT returns 401, never 200 with a degraded tier.
//! (Routes that are `#[public]` return 200 as anonymous — which is correct;
//! authenticated routes must never silently accept invalid JWTs.)

#![allow(clippy::unwrap_used)]

mod common;
use common::jwt_helpers::make_test_config;

use archiviste_gateway::{router, state::AppState};
use axum::body::Body;
use axum::http::{Request, StatusCode};
use proptest::prelude::*;
use std::sync::Arc;
use tower::ServiceExt;

fn make_state() -> Arc<AppState> {
    let mut config = make_test_config("http://127.0.0.1:1");
    config.request_timeout_ms = 5_000;
    Arc::new(AppState::new(config).unwrap())
}

proptest! {
    /// INV-6: arbitrary strings passed as Authorization Bearer on an auth-required route
    /// must never return 200. They must return 401.
    #[test]
    fn inv6_invalid_bearer_never_returns_200(
        token in "[a-zA-Z0-9+/=._\\-]{1,200}"
    ) {
        let rt = tokio::runtime::Runtime::new().unwrap();
        let status = rt.block_on(async {
            let app = router(make_state());
            let resp = app
                .oneshot(
                    Request::builder()
                        .method("GET")
                        .uri("/v1/author-test")
                        .header("authorization", format!("Bearer {token}"))
                        .body(Body::empty())
                        .unwrap(),
                )
                .await
                .unwrap();
            resp.status()
        });

        // INV-6: must not be 200 with degraded tier
        prop_assert_ne!(status, StatusCode::OK);
    }
}
