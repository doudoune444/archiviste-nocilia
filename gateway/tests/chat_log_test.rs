//! AC-13 log capture test for `POST /v1/chat` (GEN-002).
//!
//! Isolated in its own test binary so the custom tracing subscriber installed
//! via `set_default` is the only subscriber in the process.  Running AC-13 in
//! the same binary as the other chat tests caused flakiness because
//! multi-thread tokio runtimes from sibling tests emit events on different OS
//! threads that do not have the thread-local subscriber active.

#![allow(clippy::unwrap_used)]

mod common;
use common::jwt_helpers::make_app_state;

use archiviste_gateway::{router, state::AppState};
use axum::body::Body;
use axum::http::{Request, StatusCode};
use http_body_util::BodyExt;
use std::sync::{Arc, Mutex};
use tower::ServiceExt;

fn make_state(workers_url: &str) -> Arc<AppState> {
    make_app_state(workers_url)
}

async fn post_chat(app: axum::Router, body: &str) -> axum::response::Response {
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

/// `io::Write` adapter backed by a shared `Arc<Mutex<Vec<u8>>>` buffer.
struct LogWriter(Arc<Mutex<Vec<u8>>>);

impl std::io::Write for LogWriter {
    fn write(&mut self, buf: &[u8]) -> std::io::Result<usize> {
        self.0.lock().unwrap().extend_from_slice(buf);
        Ok(buf.len())
    }

    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}

/// AC-13: each request emits exactly one structured JSON log with keys
/// `event="chat"`, `request_id`, `query_len`, `upstream_status`, `status`,
/// `latency_ms`. The raw `query` string sent by the client MUST NOT appear
/// in the log output (A09 — no PII/prompt leak).
///
/// This test is non-async so it can build its own `current_thread` runtime
/// after installing the capturing subscriber, guaranteeing the subscriber is
/// active on the same OS thread for every poll of every future.
#[test]
fn ac13_log_emits_structured_chat_event_without_raw_query() {
    use tracing_subscriber::{fmt, layer::SubscriberExt};

    // Unique probe token — we assert it never appears in the captured output.
    const PROBE_QUERY: &str = "PROBE_QUERY_TOKEN_XYZ_AC13";

    let log_buf = Arc::new(Mutex::new(Vec::<u8>::new()));
    let log_buf_writer = Arc::clone(&log_buf);
    let make_writer = move || LogWriter(Arc::clone(&log_buf_writer));

    // Build subscriber before installing — no async context yet.
    let subscriber = tracing_subscriber::registry().with(
        fmt::layer()
            .json()
            .with_writer(make_writer)
            .with_target(false),
    );

    // Install as process-wide global (this binary has exactly one test, so
    // there is no concurrent subscriber conflict).
    let _guard = tracing::subscriber::set_default(subscriber);

    // `new_current_thread` keeps every future on this OS thread, which has
    // the subscriber installed above.
    let rt = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .unwrap();

    rt.block_on(async {
        let mut server = mockito::Server::new_async().await;
        let _mock = server
            .mock("POST", "/v1/generate")
            .with_status(200)
            .with_header("content-type", "application/json")
            .with_body(r#"{"answer":"ok","citations":[]}"#)
            .create_async()
            .await;

        let app = router(make_state(&server.url()));
        let payload = format!(r#"{{"query":"{PROBE_QUERY}"}}"#);

        let resp = post_chat(app, &payload).await;
        assert_eq!(resp.status(), StatusCode::OK);

        // Drain body to ensure handler has fully completed.
        let _ = resp.into_body().collect().await.unwrap().to_bytes();
    });

    let raw = log_buf.lock().unwrap().clone();
    let log_output = String::from_utf8(raw).unwrap();

    // AC-13: structured log must contain all required keys.
    assert!(
        log_output.contains(r#""event":"chat""#),
        "expected event=chat in log; got:\n{log_output}"
    );
    assert!(
        log_output.contains("request_id"),
        "expected request_id in log; got:\n{log_output}"
    );
    assert!(
        log_output.contains("query_len"),
        "expected query_len in log; got:\n{log_output}"
    );
    assert!(
        log_output.contains("upstream_status"),
        "expected upstream_status in log; got:\n{log_output}"
    );
    assert!(
        log_output.contains("latency_ms"),
        "expected latency_ms in log; got:\n{log_output}"
    );

    // AC-13 / A09: raw query string must NEVER appear in logs.
    assert!(
        !log_output.contains(PROBE_QUERY),
        "raw query leaked into log output:\n{log_output}"
    );
}
